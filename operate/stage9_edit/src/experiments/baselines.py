"""阶段9 B0-B5 六个基线的统一实现。

方法定义（按研究方案 §17）：
- B0: 固定模板，不使用大语言模型；
- B1: 大模型直接回答，不检索法规；
- B2: 普通检索增强生成；
- B3: 结构化单智能体；
- B4: 多智能体但无独立内容审查；
- B5: 完整 RiskProfile-Agent。

所有方法共用同一个输入画像、知识库、检索器和输出日志，便于公平比较。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.experiments.dataset_loader import load_profiles_with_risk
from src.experiments.retrieval_adapter import Stage9RetrievalAdapter

from src.agents.audit_agent import AuditAgent  # noqa: E402
from src.agents.profile_agent import ProfileAgent  # noqa: E402
from src.agents.review_agent import ReviewAgent  # noqa: E402
from src.agents.semantic_audit_agent import SemanticAuditAgent  # noqa: E402
from src.common.pydantic_schemas import ReviewCard, RetrievalResult  # noqa: E402
from src.llm.client import LLMClient, get_llm_client  # noqa: E402
from src.orchestrator import OrchestratorGraph  # noqa: E402


class FakeLLMClient:
    """离线干跑使用的确定性假模型，不依赖真实 Qwen。"""

    model = "fake-stage9"

    def __init__(self) -> None:
        self._n = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self._n += 1
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


class BaselineRunner:
    def __init__(self, config: dict[str, Any], root: Path | str):
        self.config = config
        self.root = Path(root)
        paths = config["paths"]
        self.profile_agent = ProfileAgent(
            whitelist_path=str(self.root / paths["whitelist"]),
            strict=True,
        )
        self.retrieval = Stage9RetrievalAdapter(
            root=self.root,
            top_k=config["retrieval"]["top_k"],
            use_rag=bool(config["retrieval"].get("use_rag", True)),
        )
        self.llm_client = get_llm_client(config)
        self.audit_agent = AuditAgent(
            forbidden_patterns=config["audit"]["forbidden_patterns"],
            max_rounds=config["audit"]["max_rounds"],
            forbidden_rules_path=str(self.root / config["audit"]["forbidden_rules_path"]),
        )
        self.review_template = ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            use_llm=False,
            prompt_path=str(self.root / config["prompts"]["review_agent"]),
            prompt_version=config["review"].get("prompt_id", "review_agent_v1"),
        )
        self.review_llm = ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            llm_client=self.llm_client,
            use_llm=True,
            prompt_path=str(self.root / config["prompts"]["review_agent"]),
            prompt_version=config["review"].get("prompt_id", "review_agent_v1"),
            fail_on_llm_error=False,
        )
        self.semantic_agent = SemanticAuditAgent(
            llm_client=self.llm_client,
            use_llm=bool(config.get("semantic_audit", {}).get("use_llm", False)),
            prompt_path=str(self.root / config["prompts"]["semantic_audit"]),
            prompt_version=config.get("semantic_audit", {}).get("prompt_id", "semantic_audit_v1"),
        )

    def _facts(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        return self.profile_agent.run(profile)["facts"]

    def _retrieve(
        self,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
    ) -> RetrievalResult:
        return self.retrieval.run(
            profile.get("historical_standard_codes") or [],
            profile.get("historical_risk_categories") or [],
            query_id=profile.get("sample_id", "q0"),
            profile_facts=facts,
        )

    def _direct_llm_points(
        self,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
    ) -> list[dict[str, Any]]:
        """B1/B3 直接调用 LLM 并解析复核点。"""
        messages = [
            {
                "role": "system",
                "content": (
                    "你是职业安全检查复核建议生成器。只输出 JSON，格式为 "
                    '{"review_points":[{"point_id":"point_1","focus_zh":"...",'
                    '"basis_profile_facts":["profile:field"],"regulation_refs":[],'
                    '"missing_field_info":[],"verification_instructions_zh":"..."}]}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "profile": profile,
                        "profile_facts": facts,
                        "evidence": [item.model_dump() for item in retrieval.items],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            text = self.llm_client.generate(messages)
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
            data = json.loads(text)
            raw = data.get("review_points") or []
        except Exception:
            return []
        points: list[dict[str, Any]] = []
        for i, item in enumerate(raw[: self.config["review"]["max_points"]], start=1):
            if not isinstance(item, dict):
                continue
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

    def _draft_from_points(
        self,
        profile: dict[str, Any],
        retrieval: RetrievalResult,
        points: list[dict[str, Any]],
        method: str,
    ) -> dict[str, Any]:
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
                {
                    "evidence_id": item.evidence_id,
                    "document_id": item.document_id,
                    "section": item.section,
                    "source_url": item.source_url,
                }
                for item in retrieval.items
            ],
            "missing_information": missing,
            "evidence_ledger": _points_to_ledger(points),
            "model": method,
            "prompt_version": "stage9-draft-v0",
            "evidence_sufficient": len(retrieval.items) > 0,
        }

    def _pack(
        self,
        method: str,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        draft: dict[str, Any],
        audit: Any,
        semantic: dict[str, Any] | None,
        final_verdict: str,
        latency_ms: float,
    ) -> dict[str, Any]:
        result = {
            "method": method,
            "sample_id": profile.get("sample_id"),
            "quarter": profile.get("quarter"),
            "final_verdict": final_verdict,
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
        return result

    def run_b0(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        draft = self.review_template.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack(
            "B0", profile, facts, retrieval, draft, audit, None,
            audit.overall_verdict, (time.perf_counter() - t0) * 1000,
        )

    def run_b1(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = RetrievalResult(
            query_id=profile.get("sample_id", "q0"),
            standard_number="UNKNOWN",
            risk_categories=[],
            items=[],
            empty_reason="B1 不检索法规",
        )
        points = self._direct_llm_points(profile, facts, retrieval)
        if not points:
            points = [self.review_template._human_review_point(retrieval)]
        draft = self._draft_from_points(profile, retrieval, points, "B1")
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack(
            "B1", profile, facts, retrieval, draft, audit, None,
            audit.overall_verdict, (time.perf_counter() - t0) * 1000,
        )

    def run_b2(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        draft = self.review_llm.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack(
            "B2", profile, facts, retrieval, draft, audit, None,
            audit.overall_verdict, (time.perf_counter() - t0) * 1000,
        )

    def run_b3(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        points = self._direct_llm_points(profile, facts, retrieval)
        if not points:
            points = [self.review_template._human_review_point(retrieval)]
        draft = self._draft_from_points(profile, retrieval, points, "B3")
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack(
            "B3", profile, facts, retrieval, draft, audit, None,
            audit.overall_verdict, (time.perf_counter() - t0) * 1000,
        )

    def run_b4(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        draft = self.review_llm.run(profile, facts, retrieval)
        audit = self.audit_agent.run(draft, facts, retrieval, profile)
        return self._pack(
            "B4", profile, facts, retrieval, draft, audit, None,
            audit.overall_verdict, (time.perf_counter() - t0) * 1000,
        )

    def run_b5(self, profile: dict[str, Any]) -> dict[str, Any]:
        t0 = time.perf_counter()
        orch = OrchestratorGraph(
            self.config,
            self.root,
            agents={
                "retrieval": self.retrieval,
                "review": self.review_llm,
                "semantic": self.semantic_agent,
            },
        )
        result = orch.run(profile, run_name="stage9_b5")
        card = result["card"]
        facts = self._facts(profile)
        retrieval = self._retrieve(profile, facts)
        return {
            "method": "B5",
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
            "run_dir": result["run_dir"],
        }

    def run_method(self, method: str, profile: dict[str, Any]) -> dict[str, Any]:
        runner = {
            "B0": self.run_b0,
            "B1": self.run_b1,
            "B2": self.run_b2,
            "B3": self.run_b3,
            "B4": self.run_b4,
            "B5": self.run_b5,
        }
        if method not in runner:
            raise ValueError(f"未知方法：{method}")
        return runner[method](profile)

    def run_profile(self, profile: dict[str, Any], methods: list[str]) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        for method in methods:
            outputs[method] = self.run_method(method, profile)
        return outputs
