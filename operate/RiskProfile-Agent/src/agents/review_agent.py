"""复核建议模块（Review Agent）——阶段1规则模板占位版。

生成最多三项人工复核重点；每项包含：关注什么、依据哪些画像事实、
对应哪些官方标准、缺少哪些现场信息、建议人工怎样核实。
阶段8将把本模块替换为 LLM 实现（v0 只保证接口与结构正确，不承诺内容质量）。
"""

from __future__ import annotations

from typing import Any, Optional

from src.common.pydantic_schemas import RetrievalResult
from src.llm.client import LLMClient


class ReviewAgent:
    def __init__(
        self,
        max_points: int = 3,
        model: str = "v0-rule-template",
        llm_client: Optional[LLMClient] = None,
        use_llm: bool = False,
    ):
        self.max_points = max_points
        self.model = model
        self.llm_client = llm_client
        self.use_llm = use_llm

    @staticmethod
    def _fact_by_field(facts: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
        for fact in facts:
            if fact["field"] == field:
                return fact
        return None

    @staticmethod
    def _evidence_ids(retrieval: RetrievalResult) -> list[str]:
        return [item.evidence_id for item in retrieval.items]

    def run(
        self,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
    ) -> dict[str, Any]:
        # 阶段1 use_llm=False，走确定性模板；阶段8将 use_llm=True 并调用 self.llm_client。
        # 模型调用不写死在本模块内部，统一通过 self.llm_client.generate()。
        if self.use_llm and self.llm_client is not None:
            raise NotImplementedError("Review 的 LLM 路径在阶段8实现；当前阶段1使用规则模板")

        sample_id = profile["sample_id"]
        risk_fact = self._fact_by_field(facts, "historical_risk_categories")
        risk_categories = list(risk_fact["value"] or []) if risk_fact else []
        codes_fact = self._fact_by_field(facts, "historical_standard_codes")
        standard_codes = list(codes_fact["value"] or []) if codes_fact else []
        evidence_ids = self._evidence_ids(retrieval)

        positives_365 = (self._fact_by_field(facts, "positives_365d") or {}).get("value", 0) or 0
        positives_730 = (self._fact_by_field(facts, "positives_730d") or {}).get("value", 0) or 0
        history_positive = (self._fact_by_field(facts, "history_positive_inspections") or {}).get("value", 0) or 0
        days_since = (self._fact_by_field(facts, "days_since_last_inspection") or {}).get("value")

        missing: list[dict[str, str]] = []
        if profile.get("no_history_flag"):
            missing.append({"field": "history_inspections", "reason": "没有历史检查记录"})
        if profile.get("insufficient_evidence_flag"):
            missing.append({"field": "insufficient_evidence", "reason": "可用依据不足，建议转人工"})
        if profile.get("entity_match_uncertain_flag"):
            missing.append({"field": "entity_match", "reason": "单位匹配存在不确定性"})
        if retrieval.empty_reason:
            missing.append({"field": "regulation_evidence", "reason": retrieval.empty_reason})

        points: list[dict[str, Any]] = []

        # 重点1：电气危险/带电防护类（R1 或 1910.269）
        if "R1" in risk_categories or any(c.startswith("1910.269") for c in standard_codes):
            points.append(
                {
                    "point_id": "point_1",
                    "focus_zh": "历史记录涉及电气危险与带电防护类风险（R1），建议人工核对现场带电作业许可与防护措施",
                    "basis_profile_facts": [f["provenance"] for f in facts if f["field"] in {"historical_risk_categories", "historical_standard_codes"}],
                    "regulation_refs": [eid for eid in evidence_ids if "1910.269" in eid],
                    "missing_field_info": ["现场实际作业情况不在公开画像数据中"],
                    "verification_instructions_zh": "核对现场是否执行带电作业安全程序，并对照 1910.269 相应条款检查作业记录",
                }
            )

        # 重点2：近期变化（近期出现违章记录或检查间隔短）
        recent = (positives_365 > 0 or positives_730 > 0) or (
            days_since is not None and float(days_since) <= 90
        )
        if recent:
            points.append(
                {
                    "point_id": "point_2",
                    "focus_zh": "近期历史出现签发违章记录的检查或检查间隔较近，建议核对近期作业程序执行与整改情况",
                    "basis_profile_facts": [f["provenance"] for f in facts if f["field"] in {"positives_365d", "positives_730d", "days_since_last_inspection"}],
                    "regulation_refs": evidence_ids[:1],
                    "missing_field_info": ["近期整改结果与现场管理措施未知"],
                    "verification_instructions_zh": "调取最近一次检查与整改记录，确认违章事项是否已关闭",
                }
            )

        # 重点3：历史整体违章情况
        if history_positive > 0 and len(points) < self.max_points:
            points.append(
                {
                    "point_id": "point_3",
                    "focus_zh": "历史存在签发违章记录的检查，建议结合历史风险类别核对现场合规情况",
                    "basis_profile_facts": [f["provenance"] for f in facts if f["field"] in {"history_positive_inspections", "smoothed_positive_rate"}],
                    "regulation_refs": evidence_ids[:2],
                    "missing_field_info": ["未在公开数据中看到现场整改与隐患闭环信息"],
                    "verification_instructions_zh": "对照历史违章涉及的标准条款逐项核实现场状况",
                }
            )

        # 兜底：没有任何可写重点时给出一条"证据不足"说明
        if not points:
            points.append(
                {
                    "point_id": "point_1",
                    "focus_zh": "画像缺乏可支撑的历史事实与法规证据，建议先人工确认单位背景再安排检查",
                    "basis_profile_facts": [],
                    "regulation_refs": [],
                    "missing_field_info": ["历史检查记录缺失", "法规证据未检索到"],
                    "verification_instructions_zh": "人工确认单位行业、历史与监管背景后再决定检查重点",
                }
            )

        points = points[: self.max_points]

        ledger = [
            {
                "claim_id": p["point_id"],
                "statement_zh": p["focus_zh"],
                "evidence_refs": p["basis_profile_facts"] + p["regulation_refs"],
                "status": "supported" if (p["basis_profile_facts"] or p["regulation_refs"]) else "unsupported",
            }
            for p in points
        ]

        return {
            "sample_id": sample_id,
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
            "evidence_ledger": ledger,
            "model": self.model,
        }
