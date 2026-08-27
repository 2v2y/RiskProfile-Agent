"""阶段8 流程控制器（Orchestrator）—— LangGraph 固定状态机。

固定流程：profile -> retrieval -> review -> audit(确定性程序核对 + 独立语义审查)
         -> PASS / DEFER(转人工 HUMAN_REVIEW) / REJECT

约束：
1. 流程固定，每个节点输入/输出明确（见 agent_registry.yaml）；
2. 限制最大审计/修改轮次，超过最大轮次必须停止并转人工，不允许无限循环；
3. 法规证据为空时失败关闭（fail close），直接转人工；
4. 智能模块不得修改风险分数、候选排序、数据划分和原始输入；
5. 每步记录：输入、输出、模型、Prompt版本、工具调用、运行时间、文字量、错误、状态变化。

LangGraph 不可用时（例如未安装）自动降级为同语义的确定性解释器，保证流程可运行。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, TypedDict

from src.agents.audit_agent import AuditAgent
from src.agents.profile_agent import ProfileAgent
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.review_agent import ReviewAgent
from src.agents.semantic_audit_agent import SemanticAuditAgent
from src.common.pydantic_schemas import RetrievalResult, ReviewCard
from src.common.run_log import RunLog, append_run_index, new_run_dir, write_output_manifest
from src.llm.client import get_llm_client

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # noqa: BLE001
    LANGGRAPH_AVAILABLE = False


class OrchestratorState(TypedDict, total=False):
    input_profile: dict[str, Any]
    sample_id: str
    profile_facts: list[dict[str, Any]]
    retrieval: dict[str, Any]
    draft_review: dict[str, Any]
    audit: dict[str, Any]
    semantic_audit: dict[str, Any]
    combined_verdict: str
    attempts: int
    max_attempts: int
    stop_loop: bool
    final_verdict: str
    human_review_reasons: list[str]
    errors: list[str]
    steps: list[dict[str, Any]]
    card: dict[str, Any]
    run_dir: str


def human_review_point(retrieval: RetrievalResult | None = None) -> dict[str, Any]:
    """证据不足时生成的人工复核点：不引用任何法规证据，禁止强行给建议。"""
    reason = (retrieval.empty_reason if retrieval else None) or "证据不足"
    return {
        "point_id": "point_1",
        "focus_zh": "证据不足，无法生成有法规依据的复核建议，建议人工确认后转人工复核",
        "basis_profile_facts": [],
        "regulation_refs": [],
        "missing_field_info": ["法规证据未检索到或不足", reason],
        "verification_instructions_zh": "由人工确认单位历史、监管背景与现场情况后，再决定检查重点",
    }


def rejection_point() -> dict[str, Any]:
    """输出被拒绝时的最终建议卡复核点：只说明拒绝，不保留被拒内容。"""
    return {
        "point_id": "point_1",
        "focus_zh": "输出包含禁止性表达或严重证据冲突，系统已拒绝生成复核建议，转人工复核",
        "basis_profile_facts": [],
        "regulation_refs": [],
        "missing_field_info": ["被拒绝的原始输出保留在审计记录（audit.json）中", "需人工核对原始输出与证据清单"],
        "verification_instructions_zh": "人工查看审计记录中的拒绝原因，核对原始输出与证据清单后再决定处理方式",
    }


class OrchestratorGraph:
    def __init__(
        self,
        config: dict[str, Any],
        root: Path,
        agents: dict[str, Any] | None = None,
        use_langgraph: bool | None = None,
    ):
        self.config = config
        self.root = Path(root)
        self.log: RunLog | None = None
        self.config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        paths = config["paths"]
        self.profile_agent = (agents or {}).get("profile") or ProfileAgent(
            whitelist_path=paths["whitelist"],
            strict=True,
        )
        self.retrieval_agent = (agents or {}).get("retrieval") or RetrievalAgent(
            chunks_path=paths["knowledge_chunks"],
            mapping_path=paths["standard_mapping"],
            top_k=config["retrieval"]["top_k"],
            min_score=config["retrieval"]["min_score"],
            method=config["retrieval"].get("method", "tfidf-standard-restricted"),
        )
        self.review_agent = (agents or {}).get("review") or ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            llm_client=get_llm_client(config),
            use_llm=bool(config["review"].get("use_llm", False)),
            prompt_path=config["prompts"]["review_agent"],
            prompt_version=config["review"].get("prompt_id", "review_agent_v1"),
            fail_on_llm_error=bool(config["review"].get("fail_on_llm_error", False)),
        )
        self.audit_agent = (agents or {}).get("audit") or AuditAgent(
            forbidden_patterns=config["audit"]["forbidden_patterns"],
            max_rounds=config["audit"]["max_rounds"],
            forbidden_rules_path=config["audit"]["forbidden_rules_path"],
        )
        self.semantic_agent = (agents or {}).get("semantic") or SemanticAuditAgent(
            llm_client=get_llm_client(config),
            use_llm=bool(config.get("semantic_audit", {}).get("use_llm", False)),
            prompt_path=config["prompts"]["semantic_audit"],
            prompt_version=config.get("semantic_audit", {}).get("prompt_id", "semantic_audit_v1"),
        )

        orch = config.get("orchestrator", {})
        self.max_attempts = int(orch.get("max_attempts") or config["audit"]["max_rounds"])
        self.fail_close_on_empty_evidence = bool(orch.get("fail_close_on_empty_evidence", True))
        self.write_run_index = bool(orch.get("write_run_index", True))

        self.agent_versions, self.prompt_versions = self._load_registries()
        self.use_langgraph = (
            use_langgraph
            if use_langgraph is not None
            else bool(orch.get("use_langgraph", True)) and LANGGRAPH_AVAILABLE
        )
        self.graph = self._build_graph() if self.use_langgraph else None

    # ---------------------------------------------------------------- 基础设施
    def _load_registries(self) -> tuple[dict[str, str], dict[str, str]]:
        agent_versions: dict[str, str] = {}
        prompt_versions: dict[str, str] = {}
        reg = self.config.get("registries", {})
        agent_path = Path(reg.get("agent_registry", "config/agent_registry.yaml"))
        prompt_path = Path(reg.get("prompt_registry", "config/prompt_registry.yaml"))
        try:
            import yaml

            if agent_path.exists():
                data = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
                agent_versions = {
                    a["name"]: str(a.get("version", "")) for a in data.get("agents", [])
                }
            if prompt_path.exists():
                data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
                prompt_versions = {
                    p["prompt_id"]: str(p.get("version", "")) for p in data.get("prompts", [])
                }
        except Exception:  # noqa: BLE001  yaml 缺失/解析失败时版本登记为空，不阻塞运行
            pass
        return agent_versions, prompt_versions

    def _leakage_precheck(self, profile: dict[str, Any]) -> None:
        forbidden = [
            k
            for k in profile
            if k.startswith("future_")
            or k.startswith("gold_")
            or k in {"label", "label_available_date", "future_citation_label", "future_citation_categories"}
        ]
        if forbidden:
            raise ValueError(f"输入包含禁止字段（可能泄漏未来信息）：{forbidden}")

    @staticmethod
    def _step(
        module: str,
        *,
        model: str,
        prompt_version: str,
        tool_calls: list[str],
        input_chars: int,
        output_chars: int,
        latency_ms: float,
        error: str | None = None,
        state_change: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "module": module,
            "model": model,
            "prompt_version": prompt_version,
            "tool_calls": tool_calls,
            "input_chars": input_chars,
            "output_chars": output_chars,
            "latency_ms": round(latency_ms, 2),
            "error": error,
            "state_change": state_change,
        }

    def _record(self, state: OrchestratorState, step: dict[str, Any]) -> list[dict[str, Any]]:
        steps = list(state.get("steps", [])) + [step]
        if self.log is not None:
            self.log.log(
                "module_end",
                run_id=Path(state["run_dir"]).name,
                sample_id=state.get("sample_id"),
                module=step["module"],
                model=step["model"],
                prompt_version=step["prompt_version"],
                tool_calls=step["tool_calls"],
                input_chars=step["input_chars"],
                output_chars=step["output_chars"],
                latency_ms=step["latency_ms"],
                error=step["error"],
                state_change=step["state_change"],
            )
        return steps

    # ---------------------------------------------------------------- 节点
    def node_profile(self, state: OrchestratorState) -> dict[str, Any]:
        t0 = time.perf_counter()
        profile = state["input_profile"]
        out = self.profile_agent.run(profile)
        step = self._step(
            "profile",
            model="none",
            prompt_version="profile_agent_v1",
            tool_calls=["whitelist_check"],
            input_chars=len(json.dumps(profile, ensure_ascii=False)),
            output_chars=len(json.dumps(out["facts"], ensure_ascii=False)),
            latency_ms=(time.perf_counter() - t0) * 1000,
            state_change={"n_facts": out["n_facts"]},
        )
        return {
            "sample_id": out["sample_id"],
            "profile_facts": out["facts"],
            "steps": self._record(state, step),
        }

    def node_retrieval(self, state: OrchestratorState) -> dict[str, Any]:
        t0 = time.perf_counter()
        profile = state["input_profile"]
        retrieval = self.retrieval_agent.run(
            profile.get("historical_standard_codes") or [],
            profile.get("historical_risk_categories") or [],
            query_id=state["sample_id"],
            profile_facts=state.get("profile_facts", []),
        )
        retrieval_dict = retrieval.model_dump()
        step = self._step(
            "retrieval",
            model="none",
            prompt_version="retrieval_agent_v1",
            tool_calls=["regulation_chunks.jsonl", "standard_document_mapping.csv"],
            input_chars=len(json.dumps(profile.get("historical_standard_codes") or [], ensure_ascii=False)),
            output_chars=len(json.dumps(retrieval_dict, ensure_ascii=False)),
            latency_ms=(time.perf_counter() - t0) * 1000,
            state_change={
                "n_evidence": len(retrieval.items),
                "empty_reason": retrieval.empty_reason,
            },
        )
        return {"retrieval": retrieval_dict, "steps": self._record(state, step)}

    def _empty_evidence_draft(
        self,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        sample_id: str,
    ) -> dict[str, Any]:
        point = human_review_point(retrieval)
        missing: list[dict[str, str]] = []
        if profile.get("no_history_flag"):
            missing.append({"field": "history_inspections", "reason": "没有历史检查记录"})
        if profile.get("insufficient_evidence_flag"):
            missing.append({"field": "insufficient_evidence", "reason": "可用依据不足，建议转人工"})
        if profile.get("entity_match_uncertain_flag"):
            missing.append({"field": "entity_match", "reason": "单位匹配存在不确定性"})
        if retrieval.empty_reason:
            missing.append({"field": "regulation_evidence", "reason": retrieval.empty_reason})
        return {
            "sample_id": sample_id,
            "quarter": profile.get("quarter"),
            "ranking_cutoff": profile.get("ranking_cutoff"),
            "review_points": [point],
            "official_citations": [],
            "missing_information": missing,
            "evidence_ledger": [
                {
                    "claim_id": "point_1",
                    "statement_zh": point["focus_zh"],
                    "evidence_refs": [],
                    "status": "deferred",
                }
            ],
            "model": "v0-rule-template",
            "prompt_version": self.review_agent.prompt_version,
            "evidence_sufficient": False,
        }

    def node_review(self, state: OrchestratorState) -> dict[str, Any]:
        t0 = time.perf_counter()
        profile = state["input_profile"]
        facts = state.get("profile_facts", [])
        retrieval = RetrievalResult.model_validate(state["retrieval"])
        if self.fail_close_on_empty_evidence and not retrieval.items:
            draft = self._empty_evidence_draft(profile, facts, retrieval, state["sample_id"])
            model = "v0-rule-template"
        else:
            draft = self.review_agent.run(profile, facts, retrieval)
            model = draft.get("model", getattr(self.review_agent, "model", "unknown"))
        step = self._step(
            "review",
            model=str(model),
            prompt_version=str(draft.get("prompt_version", getattr(self.review_agent, "prompt_version", ""))),
            tool_calls=[],
            input_chars=len(json.dumps({"facts": facts, "retrieval": state["retrieval"]}, ensure_ascii=False)),
            output_chars=len(json.dumps(draft, ensure_ascii=False)),
            latency_ms=(time.perf_counter() - t0) * 1000,
            state_change={"n_points": len(draft.get("review_points", []))},
        )
        return {"draft_review": draft, "steps": self._record(state, step)}

    def node_audit(self, state: OrchestratorState) -> dict[str, Any]:
        t0 = time.perf_counter()
        profile = state["input_profile"]
        facts = state.get("profile_facts", [])
        retrieval = RetrievalResult.model_validate(state["retrieval"])
        attempts = int(state.get("attempts", 0)) + 1
        audit = self.audit_agent.run(state["draft_review"], facts, retrieval, profile)
        audit_dict = audit.model_dump()
        semantic = self.semantic_agent.run(state["draft_review"], facts, retrieval, audit)
        combined = self._combine_verdicts(audit.overall_verdict, semantic["overall_verdict"])
        stop_loop = self.fail_close_on_empty_evidence and not retrieval.items
        step = self._step(
            "audit",
            model=f"program:{self.audit_agent.__class__.__name__},semantic:{semantic.get('provider')}",
            prompt_version=f"audit_agent_v1,semantic:{semantic.get('prompt_version')}",
            tool_calls=["forbidden_claim_rules.yaml", "profile_facts", "evidence_ledger"],
            input_chars=len(json.dumps(state["draft_review"], ensure_ascii=False)),
            output_chars=len(json.dumps(audit_dict, ensure_ascii=False)),
            latency_ms=(time.perf_counter() - t0) * 1000,
            state_change={
                "deterministic_verdict": audit.overall_verdict,
                "semantic_verdict": semantic["overall_verdict"],
                "combined_verdict": combined,
                "attempts": attempts,
            },
        )
        return {
            "audit": audit_dict,
            "semantic_audit": semantic,
            "combined_verdict": combined,
            "attempts": attempts,
            "stop_loop": stop_loop,
            "steps": self._record(state, step),
        }

    @staticmethod
    def _combine_verdicts(deterministic: str, semantic: str) -> str:
        if "REJECT" in (deterministic, semantic):
            return "REJECT"
        if "DEFER" in (deterministic, semantic):
            return "DEFER"
        return "PASS"

    def _route(self, state: OrchestratorState) -> str:
        verdict = state.get("combined_verdict", "DEFER")
        if verdict == "REJECT":
            return "final_reject"
        if verdict == "PASS":
            return "final_pass"
        if state.get("stop_loop") or int(state.get("attempts", 0)) >= int(state.get("max_attempts", 2)):
            return "final_human_review"
        return "review"

    # ---------------------------------------------------------------- 终态
    def _per_claim(self, audit: dict[str, Any], semantic: dict[str, Any]) -> list[dict[str, Any]]:
        claims = [dict(c) for c in (audit.get("claims") or [])]
        semantic_by_id = {c["claim_id"]: c for c in (semantic.get("per_claim") or [])}
        for c in claims:
            s = semantic_by_id.get(c["claim_id"], {})
            c["semantic_verdict"] = s.get("verdict")
            c["semantic_reason"] = s.get("reason")
        return claims

    def _versions(self) -> dict[str, Any]:
        knowledge: dict[str, Any] = {"note": "学生2阶段6交付 6.0-frozen"}
        knowledge_dir = Path(self.config.get("paths", {}).get("knowledge_dir", "data/knowledge"))
        manifest_path = knowledge_dir / "knowledge_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                knowledge = {
                    "n_chunks": manifest.get("n_chunks"),
                    "generated_at": manifest.get("generated_at"),
                    "note": "学生2阶段6交付 6.0-frozen",
                }
            except Exception:  # noqa: BLE001
                pass
        return {
            "modules": [f"{name}:{ver}" for name, ver in sorted(self.agent_versions.items())],
            "model": {
                "llm_provider": self.config.get("llm", {}).get("provider", "dummy"),
                "review": self.config["review"].get("model", ""),
                "review_note": self.config["review"].get("model_note", ""),
                "semantic": self.config.get("semantic_audit", {}).get("model_note", ""),
            },
            "prompts": dict(self.prompt_versions),
            "knowledge": knowledge,
        }

    def _build_card(self, state: OrchestratorState, final_verdict: str) -> dict[str, Any]:
        profile = state["input_profile"]
        facts = state.get("profile_facts", [])
        retrieval = state.get("retrieval", {})
        draft = state.get("draft_review") or {}
        audit = state.get("audit", {})
        semantic = state.get("semantic_audit", {})

        points = list(draft.get("review_points") or [])
        if final_verdict == "REJECT":
            points = [rejection_point()]
        if final_verdict == "DEFER" and not points:
            points = [human_review_point()]

        card = {
            "sample_id": profile["sample_id"],
            "quarter": profile.get("quarter"),
            "ranking_cutoff": profile.get("ranking_cutoff"),
            "frozen_risk": {
                "risk_score": profile.get("risk_score"),
                "risk_percentile": profile.get("risk_percentile"),
                "ranking_source": "M2-frozen" if profile.get("risk_score") is not None else "pending-m2",
                "model_version": profile.get("model_version", ""),
                "score_evidence": profile.get("score_evidence", ""),
            },
            "profile_facts": facts,
            "review_points": points,
            "official_citations": [
                {
                    "evidence_id": item["evidence_id"],
                    "document_id": item["document_id"],
                    "section": item["section"],
                    "source_url": item["source_url"],
                }
                for item in (retrieval.get("items") or [])
            ],
            "missing_information": draft.get("missing_information", []),
            "evidence_ledger": draft.get("evidence_ledger", []),
            "versions": self._versions(),
            "audit": {
                "status": audit.get("overall_verdict") or final_verdict,
                "attempts": int(state.get("attempts", 0)),
                "max_attempts": int(state.get("max_attempts", self.max_attempts)),
                "per_claim": self._per_claim(audit, semantic),
            },
            "llm_used": draft.get("llm_used", False),
            "llm_source": draft.get("llm_source", "template"),
            "final_verdict": final_verdict,
        }
        ReviewCard.model_validate(card)
        return card

    def node_final_pass(self, state: OrchestratorState) -> dict[str, Any]:
        card = self._build_card(state, "PASS")
        step = self._step(
            "final",
            model="n/a",
            prompt_version="n/a",
            tool_calls=[],
            input_chars=0,
            output_chars=len(json.dumps(card, ensure_ascii=False)),
            latency_ms=0.0,
            state_change={"final_verdict": "PASS"},
        )
        return {"final_verdict": "PASS", "card": card, "steps": self._record(state, step)}

    def node_final_human_review(self, state: OrchestratorState) -> dict[str, Any]:
        card = self._build_card(state, "DEFER")
        reasons: list[str] = []
        retrieval = state.get("retrieval", {})
        if retrieval.get("empty_reason"):
            reasons.append(f"法规证据为空：{retrieval['empty_reason']}")
        if int(state.get("attempts", 0)) >= int(state.get("max_attempts", 2)):
            reasons.append(f"达到最大审计轮次（{state.get('attempts')}/{state.get('max_attempts')}），停止并转人工")
        step = self._step(
            "final",
            model="n/a",
            prompt_version="n/a",
            tool_calls=[],
            input_chars=0,
            output_chars=len(json.dumps(card, ensure_ascii=False)),
            latency_ms=0.0,
            state_change={"final_verdict": "DEFER", "reasons": reasons},
        )
        return {
            "final_verdict": "DEFER",
            "human_review_reasons": reasons,
            "card": card,
            "steps": self._record(state, step),
        }

    def node_final_reject(self, state: OrchestratorState) -> dict[str, Any]:
        card = self._build_card(state, "REJECT")
        step = self._step(
            "final",
            model="n/a",
            prompt_version="n/a",
            tool_calls=[],
            input_chars=0,
            output_chars=len(json.dumps(card, ensure_ascii=False)),
            latency_ms=0.0,
            state_change={"final_verdict": "REJECT"},
        )
        return {"final_verdict": "REJECT", "card": card, "steps": self._record(state, step)}

    # ---------------------------------------------------------------- 图
    def _build_graph(self):
        graph = StateGraph(OrchestratorState)
        graph.add_node("profile", self.node_profile)
        graph.add_node("retrieval", self.node_retrieval)
        graph.add_node("review", self.node_review)
        graph.add_node("audit", self.node_audit)
        graph.add_node("final_pass", self.node_final_pass)
        graph.add_node("final_human_review", self.node_final_human_review)
        graph.add_node("final_reject", self.node_final_reject)
        graph.add_edge(START, "profile")
        graph.add_edge("profile", "retrieval")
        graph.add_edge("retrieval", "review")
        graph.add_edge("review", "audit")
        graph.add_conditional_edges(
            "audit",
            self._route,
            {
                "final_pass": "final_pass",
                "final_human_review": "final_human_review",
                "final_reject": "final_reject",
                "review": "review",
            },
        )
        graph.add_edge("final_pass", END)
        graph.add_edge("final_human_review", END)
        graph.add_edge("final_reject", END)
        return graph.compile()

    def _fallback_run(self, state: OrchestratorState) -> OrchestratorState:
        """LangGraph 不可用时的同语义确定性解释器。"""
        order = ["profile", "retrieval", "review", "audit"]
        node = "profile"
        while node not in ("final_pass", "final_human_review", "final_reject"):
            if node == "profile":
                state.update(self.node_profile(state))
                node = "retrieval"
            elif node == "retrieval":
                state.update(self.node_retrieval(state))
                node = "review"
            elif node == "review":
                state.update(self.node_review(state))
                node = "audit"
            elif node == "audit":
                state.update(self.node_audit(state))
                node = self._route(state)
        if node == "final_pass":
            state.update(self.node_final_pass(state))
        elif node == "final_human_review":
            state.update(self.node_final_human_review(state))
        else:
            state.update(self.node_final_reject(state))
        return state

    # ---------------------------------------------------------------- 运行入口
    def run(
        self,
        profile: dict[str, Any],
        run_name: str = "stage8_e2e",
        _timestamp: str | None = None,
        _unique: str | None = None,
    ) -> dict[str, Any]:
        self._leakage_precheck(profile)
        sample_id = profile.get("sample_id", "UNKNOWN")
        run_dir = new_run_dir(
            Path(self.config["paths"]["runs"]),
            run_name,
            self.config,
            _timestamp=_timestamp,
            _unique=_unique,
        )
        log = RunLog(run_dir / "run_log.jsonl")
        self.log = log
        log.log(
            "start",
            run_id=run_dir.name,
            sample_id=sample_id,
            config_hash=self.config_hash,
            use_langgraph=self.use_langgraph,
        )

        initial: OrchestratorState = {
            "input_profile": profile,
            "sample_id": sample_id,
            "profile_facts": [],
            "retrieval": {"items": [], "empty_reason": None},
            "draft_review": {},
            "audit": {},
            "semantic_audit": {},
            "combined_verdict": "PENDING",
            "attempts": 0,
            "max_attempts": self.max_attempts,
            "stop_loop": False,
            "final_verdict": "",
            "human_review_reasons": [],
            "errors": [],
            "steps": [],
            "card": {},
            "run_dir": str(run_dir),
        }

        try:
            if self.use_langgraph:
                final_state = self.graph.invoke(initial)
            else:
                final_state = self._fallback_run(initial)
        except Exception as exc:  # noqa: BLE001
            log.log("error", run_id=run_dir.name, sample_id=sample_id, error=str(exc))
            log.close()
            self.log = None
            raise

        card = final_state["card"]
        outputs = {
            "profile_facts.json": final_state.get("profile_facts", []),
            "retrieval.json": final_state.get("retrieval", {}),
            "draft_review.json": final_state.get("draft_review", {}),
            "audit.json": final_state.get("audit", {}),
            "semantic_audit.json": final_state.get("semantic_audit", {}),
            "review_card.json": card,
            "state_trace.json": {
                "sample_id": sample_id,
                "config_hash": self.config_hash,
                "attempts": final_state.get("attempts"),
                "max_attempts": final_state.get("max_attempts"),
                "final_verdict": final_state.get("final_verdict"),
                "human_review_reasons": final_state.get("human_review_reasons", []),
                "steps": final_state.get("steps", []),
            },
        }
        written: dict[str, Path] = {}
        for name, obj in outputs.items():
            path = run_dir / name
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            written[name] = path

        manifest_path = write_output_manifest(run_dir, written)
        final_verdict = final_state.get("final_verdict", "ERROR")
        log.log(
            "end",
            run_id=run_dir.name,
            sample_id=sample_id,
            final_verdict=final_verdict,
            attempts=final_state.get("attempts"),
            max_attempts=final_state.get("max_attempts"),
            manifest=str(manifest_path.name),
        )
        log.close()
        self.log = None

        if self.write_run_index:
            append_run_index(
                Path(self.config["paths"]["runs"]),
                {
                    "run_id": run_dir.name,
                    "run_dir": str(run_dir),
                    "sample_id": sample_id,
                    "config_hash": self.config_hash,
                    "final_verdict": final_verdict,
                    "card_sha256": hashlib.sha256(
                        json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")
                    ).hexdigest(),
                },
            )

        return {
            "run_dir": str(run_dir),
            "final_verdict": final_verdict,
            "final_status": {"PASS": "PASS", "DEFER": "HUMAN_REVIEW", "REJECT": "REJECT"}.get(
                final_verdict, final_verdict
            ),
            "card": card,
            "output_manifest": str(manifest_path),
            "run_log": str(run_dir / "run_log.jsonl"),
            "state_trace": str(run_dir / "state_trace.json"),
            "human_review_reasons": final_state.get("human_review_reasons", []),
        }
