"""B0—B5 六个基线的统一实现（复用阶段八 Agent）。

定义（沿用项目既有命名）：
- B0 = Fixed Template（固定模板，不用 LLM）
- B1 = Direct LLM（普通 LLM，不检索法规）
- B2 = RAG（检索增强生成）
- B3 = Single Agent（结构化单智能体）
- B4 = Multi-Agent 但无独立语义审查
- B5 = 完整 RiskProfile-Agent（多 Agent + 独立语义审查 + 失败关闭）

所有方法使用同一批画像输入、同一套法规知识库、同一份评价规则，保证公平比较。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from adapters import paths  # noqa: F401
from adapters.retrieval_adapter import Stage9RetrievalAdapter
from src.agents.audit_agent import AuditAgent  # noqa: E402
from src.agents.profile_agent import ProfileAgent  # noqa: E402
from src.agents.review_agent import ReviewAgent  # noqa: E402
from src.common.pydantic_schemas import RetrievalResult  # noqa: E402
from src.llm.client import get_llm_client  # noqa: E402
from src.orchestrator.graph import OrchestratorGraph  # noqa: E402


class FakeLLM:
    """离线干跑确定性假模型：不依赖 Qwen，返回固定的复核点 JSON。"""

    model = "fake-stage9"
    provider = "dummy"

    def generate(self, messages: list[dict[str, str]]) -> str:
        return json.dumps(
            {
                "review_points": [
                    {
                        "point_id": "point_1",
                        "focus_zh": "基于已有画像事实与法规证据，建议人工核对现场风险防护措施",
                        "basis_profile_facts": ["profile:history_inspections"],
                        "regulation_refs": [],
                        "missing_field_info": ["现场实际作业情况不在公开画像数据中"],
                        "verification_instructions_zh": "调取现场记录，核对防护措施是否落实",
                    }
                ]
            },
            ensure_ascii=False,
        )


def _points_to_ledger(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for p in points:
        refs = list(p.get("basis_profile_facts", [])) + list(p.get("regulation_refs", []))
        ledger.append(
            {
                "claim_id": p.get("point_id", "point_1"),
                "statement_zh": p.get("focus_zh", ""),
                "evidence_refs": refs,
                "status": "supported" if refs else "unsupported",
            }
        )
    return ledger


class BaseBaseline:
    def __init__(self, config: dict[str, Any], data: dict[str, Path]):
        self.config = config
        self.data = data
        paths_cfg = config["paths"]
        self.profile_agent = ProfileAgent(whitelist_path=paths_cfg["whitelist"], strict=True)
        self.retrieval = Stage9RetrievalAdapter(
            data, top_k=config["retrieval"]["top_k"], use_rag=bool(config["retrieval"].get("use_rag", True))
        )
        self.audit_agent = AuditAgent(
            forbidden_patterns=config["audit"]["forbidden_patterns"],
            max_rounds=config["audit"]["max_rounds"],
            forbidden_rules_path=config["audit"].get("forbidden_rules_path"),
        )
        self.review_template = ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            use_llm=False,
            prompt_path=config["prompts"]["review_agent"],
            prompt_version=config["review"].get("prompt_id", "review_agent_v1"),
        )
        self.review_llm = ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            llm_client=self._llm_client(),
            use_llm=True,
            prompt_path=config["prompts"]["review_agent"],
            prompt_version=config["review"].get("prompt_id", "review_agent_v1"),
            fail_on_llm_error=False,
        )

    def _llm_client(self):
        if self.config.get("llm", {}).get("provider") == "qwen":
            return get_llm_client(self.config)
        return FakeLLM()

    def _review_system_prompt(self) -> str:
        p = Path(self.config["prompts"]["review_agent"])
        if p.exists():
            return p.read_text(encoding="utf-8")
        return (
            "你是职业安全检查复核建议生成器。只输出 JSON，格式为 "
            '{"review_points":[{"point_id":"point_1","focus_zh":"...",'
            '"basis_profile_facts":["profile:field"],"regulation_refs":[],'
            '"missing_field_info":[],"verification_instructions_zh":"..."}]}'
        )

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any]:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    def _facts(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        return self.profile_agent.run(profile)["facts"]

    def _retrieve(self, profile: dict[str, Any], facts: list[dict[str, Any]]) -> RetrievalResult:
        return self.retrieval.run(
            profile.get("historical_standard_codes") or [],
            profile.get("historical_risk_categories") or [],
            query_id=profile.get("sample_id", "q0"),
            profile_facts=facts,
        )

    def _direct_llm_points(self, profile: dict[str, Any], facts: list[dict[str, Any]],
                           retrieval: RetrievalResult) -> list[dict[str, Any]]:
        # Stage9 输入预算：画像卡/事实/证据正文发送前压缩（见 src/common/prompt_budget.py）。
        from src.common.prompt_budget import compact_evidence, compact_facts, compact_profile, enforce_input_budget

        messages = [
            {"role": "system", "content": self._review_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {"profile": compact_profile(profile),
                     "profile_facts": compact_facts(facts),
                     "evidence": compact_evidence(retrieval.items)},
                    ensure_ascii=False,
                ),
            },
        ]
        enforce_input_budget(messages)
        text = self._llm_client().generate(messages)
        try:
            data = self._parse_json_response(text)
            raw = data.get("review_points") or []
        except Exception:
            return []
        points: list[dict[str, Any]] = []
        for i, item in enumerate(raw[: self.config["review"]["max_points"]], start=1):
            if isinstance(item, dict):
                points.append(
                    {
                        "point_id": item.get("point_id") or f"point_{i}",
                        "focus_zh": str(item.get("focus_zh", "")),
                        "basis_profile_facts": [str(x) for x in item.get("basis_profile_facts", [])],
                        "regulation_refs": [str(x) for x in item.get("regulation_refs", [])],
                        "missing_field_info": [str(x) for x in item.get("missing_field_info", [])],
                        "verification_instructions_zh": str(item.get("verification_instructions_zh", "")),
                    }
                )
        return points

    def _draft_from_points(self, profile: dict[str, Any], retrieval: RetrievalResult,
                           points: list[dict[str, Any]], model: str,
                           llm_used: bool = False,
                           llm_source: str = "template") -> dict[str, Any]:
        missing: list[dict[str, str]] = []
        if profile.get("no_history_flag"):
            missing.append({"field": "history_inspections", "reason": "没有历史检查记录"})
        if retrieval.empty_reason:
            missing.append({"field": "regulation_evidence", "reason": retrieval.empty_reason})
        return {
            "sample_id": profile["sample_id"],
            "quarter": profile.get("quarter"),
            "ranking_cutoff": profile.get("ranking_cutoff"),
            "review_points": points,
            "official_citations": [
                {"evidence_id": i.evidence_id, "document_id": i.document_id,
                 "section": i.section, "source_url": i.source_url}
                for i in retrieval.items
            ],
            "missing_information": missing,
            "evidence_ledger": _points_to_ledger(points),
            "model": model,
            "prompt_version": self.config["review"].get("prompt_id", "review_agent_v1"),
            "evidence_sufficient": len(retrieval.items) > 0,
            "llm_used": llm_used,
            "llm_source": llm_source,
        }

    def _pack(self, method: str, profile: dict[str, Any], facts: list[dict[str, Any]],
              retrieval: RetrievalResult, draft: dict[str, Any], audit: Any,
              semantic: dict[str, Any] | None, verdict: str, latency_ms: float,
              architecture: str) -> dict[str, Any]:
        return {
            "method": method,
            "architecture": architecture,
            "sample_id": profile.get("sample_id"),
            "quarter": profile.get("quarter"),
            "final_verdict": verdict,
            "profile_facts": facts,
            "retrieval": retrieval.model_dump(),
            "draft_review": draft,
            "audit": audit.model_dump() if audit is not None else None,
            "semantic_audit": semantic,
            "latency_ms": round(latency_ms, 2),
            "input_chars": len(json.dumps(profile, ensure_ascii=False)),
            "output_chars": len(json.dumps(draft, ensure_ascii=False)),
            "model": self.config.get("llm", {}).get("provider", "dummy"),
        }

    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class B0Template(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        draft = self.review_template.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack("B0", profile, facts, retrieval, draft, audit, None,
                          audit.overall_verdict, (time.perf_counter() - t0) * 1000, "template")


class B1LLM(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = RetrievalResult(query_id=profile.get("sample_id", "q0"),
                                    standard_number="UNKNOWN", risk_categories=[], items=[],
                                    empty_reason="B1 不检索法规")
        points_llm = self._direct_llm_points(profile, facts, retrieval)
        points = points_llm or [
            {"point_id": "point_1", "focus_zh": "证据不足，无法生成有法规依据的复核建议，建议人工确认后转人工复核",
             "basis_profile_facts": [], "regulation_refs": [], "missing_field_info": ["法规证据未检索到或不足"],
             "verification_instructions_zh": "由人工确认单位历史、监管背景与现场情况后再决定检查重点"}
        ]
        provider = self.config.get("llm", {}).get("provider", "dummy")
        llm_used = bool(points_llm)
        draft = self._draft_from_points(
            profile, retrieval, points, "B1",
            llm_used=llm_used,
            llm_source=("qwen" if (provider == "qwen" and llm_used) else ("dummy" if llm_used else "template_fallback")),
        )
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack("B1", profile, facts, retrieval, draft, audit, None,
                          audit.overall_verdict, (time.perf_counter() - t0) * 1000, "plain_llm")


class B2RAG(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        draft = self.review_llm.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack("B2", profile, facts, retrieval, draft, audit, None,
                          audit.overall_verdict, (time.perf_counter() - t0) * 1000, "rag")


class B3SingleAgent(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        points_llm = self._direct_llm_points(profile, facts, retrieval)
        points = points_llm or [
            {"point_id": "point_1", "focus_zh": "证据不足，无法生成有法规依据的复核建议，建议人工确认后转人工复核",
             "basis_profile_facts": [], "regulation_refs": [], "missing_field_info": ["法规证据未检索到或不足"],
             "verification_instructions_zh": "由人工确认单位历史、监管背景与现场情况后再决定检查重点"}
        ]
        provider = self.config.get("llm", {}).get("provider", "dummy")
        llm_used = bool(points_llm)
        draft = self._draft_from_points(
            profile, retrieval, points, "B3",
            llm_used=llm_used,
            llm_source=("qwen" if (provider == "qwen" and llm_used) else ("dummy" if llm_used else "template_fallback")),
        )
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack("B3", profile, facts, retrieval, draft, audit, None,
                          audit.overall_verdict, (time.perf_counter() - t0) * 1000, "single_agent")


class B4MultiAgentNoSemantic(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        if not retrieval.items:
            draft = self._draft_from_points(
                profile, retrieval,
                [{"point_id": "point_1",
                  "focus_zh": "证据不足，无法生成有法规依据的复核建议，建议人工确认后转人工复核",
                  "basis_profile_facts": [], "regulation_refs": [],
                  "missing_field_info": ["法规证据未检索到或不足"],
                  "verification_instructions_zh": "由人工确认单位历史、监管背景与现场情况后再决定检查重点"}],
                "B4",
                llm_used=False,
                llm_source="template",
            )
        else:
            draft = self.review_llm.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack("B4", profile, facts, retrieval, draft, audit, None,
                          audit.overall_verdict, (time.perf_counter() - t0) * 1000, "multi_agent_no_semantic")


class B5FullAgent(BaseBaseline):
    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        orch = OrchestratorGraph(
            self.config, Path(self.config["paths"]["runs"]).parent,
            agents={"retrieval": self.retrieval},
            use_langgraph=False,
        )
        result = orch.run(profile, run_name="stage9_b5")
        card = result["card"]
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        return {
            "method": "B5",
            "architecture": "multi_agent_full",
            "sample_id": profile.get("sample_id"),
            "quarter": profile.get("quarter"),
            "final_verdict": result["final_verdict"],
            "profile_facts": facts,
            "retrieval": retrieval.model_dump(),
            "draft_review": card,
            "audit": card.get("audit"),
            "semantic_audit": result.get("semantic_audit"),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "input_chars": len(json.dumps(profile, ensure_ascii=False)),
            "output_chars": len(json.dumps(card, ensure_ascii=False)),
            "model": self.config.get("llm", {}).get("provider", "dummy"),
        }


_REGISTRY = {
    "B0": B0Template,
    "B1": B1LLM,
    "B2": B2RAG,
    "B3": B3SingleAgent,
    "B4": B4MultiAgentNoSemantic,
    "B5": B5FullAgent,
}


def get_baseline(method: str, config: dict[str, Any], data: dict[str, Path]) -> BaseBaseline:
    if method not in _REGISTRY:
        raise ValueError(f"未知方法 {method}，可选：{sorted(_REGISTRY)}")
    return _REGISTRY[method](config, data)
