"""复核建议模块（Review Agent）。

两种工作模式：
- use_llm=False：确定性规则模板（阶段1离线联调、自动化测试用）
- use_llm=True：调用 LLMClient（服务器 Qwen）生成复核重点

模型调用不写死在本模块内部，统一通过 self.llm_client.generate()。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from src.common.pydantic_schemas import RetrievalResult
from src.llm.client import LLMClient


_SYSTEM_PROMPT = (
    "你是职业安全检查的复核建议生成器。请根据给定的画像事实和法规证据，"
    "生成最多3项人工复核重点。要求："
    "1) 只用给定材料，不得编造数字、法规或现场事实；"
    "2) 不得给出违法认定、处罚建议、事故必然性结论；"
    "3) 证据不足时明确说明缺少什么，而不是强行给建议；"
    "4) 每个复核点必须能对应到画像事实(profile:)或法规证据(regulation:)。"
    "只输出JSON，不要输出其他文字。"
)

_OUTPUT_SPEC = (
    '输出JSON格式：{"review_points":[{"point_id":"point_1",'
    '"focus_zh":"建议人工关注什么",'
    '"basis_profile_facts":["profile:字段名"],'
    '"regulation_refs":["regulation:文档ID#条款"],'
    '"missing_field_info":["缺少哪些现场信息"],'
    '"verification_instructions_zh":"建议人工怎样核实"}]}'
)


class ReviewAgent:
    def __init__(
        self,
        max_points: int = 3,
        model: str = "v0-rule-template",
        llm_client: Optional[LLMClient] = None,
        use_llm: bool = False,
        prompt_path: str | None = None,
        prompt_version: str = "review_agent_v1",
        fail_on_llm_error: bool = False,
    ):
        self.max_points = max_points
        self.model = model
        self.llm_client = llm_client
        self.use_llm = use_llm
        self.prompt_path = prompt_path
        self.prompt_version = prompt_version
        self.fail_on_llm_error = fail_on_llm_error

    def _system_prompt(self) -> str:
        if self.prompt_path:
            path = Path(self.prompt_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
        return _SYSTEM_PROMPT + "\n" + _OUTPUT_SPEC

    @staticmethod
    def _fact_by_field(facts: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
        for fact in facts:
            if fact["field"] == field:
                return fact
        return None

    @staticmethod
    def _evidence_ids(retrieval: RetrievalResult) -> list[str]:
        return [item.evidence_id for item in retrieval.items]

    def _missing(self, profile: dict[str, Any], retrieval: RetrievalResult) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        if profile.get("no_history_flag"):
            missing.append({"field": "history_inspections", "reason": "没有历史检查记录"})
        if profile.get("insufficient_evidence_flag"):
            missing.append({"field": "insufficient_evidence", "reason": "可用依据不足，建议转人工"})
        if profile.get("entity_match_uncertain_flag"):
            missing.append({"field": "entity_match", "reason": "单位匹配存在不确定性"})
        if retrieval.empty_reason:
            missing.append({"field": "regulation_evidence", "reason": retrieval.empty_reason})
        return missing

    def _template_points(
        self,
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        evidence_ids: list[str],
    ) -> list[dict[str, Any]]:
        risk_fact = self._fact_by_field(facts, "historical_risk_categories")
        risk_categories = list(risk_fact["value"] or []) if risk_fact else []
        codes_fact = self._fact_by_field(facts, "historical_standard_codes")
        standard_codes = list(codes_fact["value"] or []) if codes_fact else []

        positives_365 = (self._fact_by_field(facts, "positives_365d") or {}).get("value", 0) or 0
        positives_730 = (self._fact_by_field(facts, "positives_730d") or {}).get("value", 0) or 0
        history_positive = (self._fact_by_field(facts, "history_positive_inspections") or {}).get("value", 0) or 0
        days_since = (self._fact_by_field(facts, "days_since_last_inspection") or {}).get("value")

        points: list[dict[str, Any]] = []
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

        recent = (positives_365 > 0 or positives_730 > 0) or (days_since is not None and float(days_since) <= 90)
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

        if not points:
            points.append(self._human_review_point(retrieval))
        return points

    @staticmethod
    def _human_review_point(retrieval: RetrievalResult) -> dict[str, Any]:
        """法规证据为空/不足时生成的人工复核点：不引用任何法规证据，禁止强行给建议。"""
        reason = retrieval.empty_reason or "证据不足"
        return {
            "point_id": "point_1",
            "focus_zh": "证据不足，无法生成有法规依据的复核建议，建议人工确认后转人工复核",
            "basis_profile_facts": [],
            "regulation_refs": [],
            "missing_field_info": ["法规证据未检索到或不足", reason],
            "verification_instructions_zh": "由人工确认单位历史、监管背景与现场情况后，再决定检查重点",
        }

    def _parse_llm_json(self, text: Any) -> dict[str, Any]:
        # content 可能是字符串或内容块列表，统一转成字符串。
        if isinstance(text, list):
            text = "".join(
                str(block.get("text", "") if isinstance(block, dict) else block)
                for block in text
            )
        text = str(text).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception as exc:
            raise ValueError(f"无法解析 LLM JSON 输出，前200字符：{text[:200]}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"LLM 输出不是 JSON 对象，前200字符：{text[:200]}")
        return data

    def _llm_points(
        self,
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        evidence_ids: list[str],
    ) -> Optional[list[dict[str, Any]]]:
        # Stage9 输入预算：画像事实与法规证据正文发送前压缩（见 src/common/prompt_budget.py），
        # 保证 Qwen vLLM(max_model_len=8192) 下 input <= 6000 tokens。
        from src.common.prompt_budget import compact_evidence, compact_facts, enforce_input_budget

        user_payload = {
            "画像事实": compact_facts(facts),
            "法规证据": compact_evidence(retrieval.items),
            "证据不足原因": retrieval.empty_reason,
        }
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        enforce_input_budget(messages)
        text = self.llm_client.generate(messages)
        data = self._parse_llm_json(text)
        raw_points = data.get("review_points") or []

        valid_evidence = set(evidence_ids)
        points: list[dict[str, Any]] = []
        for index, item in enumerate(raw_points[: self.max_points], start=1):
            if not isinstance(item, dict):
                continue
            points.append(
                {
                    "point_id": item.get("point_id") or f"point_{index}",
                    "focus_zh": str(item.get("focus_zh", "")),
                    "basis_profile_facts": [str(x) for x in item.get("basis_profile_facts", [])],
                    "regulation_refs": [str(x) for x in item.get("regulation_refs", []) if str(x) in valid_evidence],
                    "missing_field_info": [str(x) for x in item.get("missing_field_info", [])],
                    "verification_instructions_zh": str(item.get("verification_instructions_zh", "")),
                }
            )
        return points or None

    def run(
        self,
        profile: dict[str, Any],
        facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
    ) -> dict[str, Any]:
        sample_id = profile["sample_id"]
        evidence_ids = self._evidence_ids(retrieval)
        missing = self._missing(profile, retrieval)

        points: Optional[list[dict[str, Any]]] = None
        if not retrieval.items:
            # 法规证据为空/不足：不得强行生成有法规依据的建议，输出人工复核点
            points = [self._human_review_point(retrieval)]
        else:
            if self.use_llm and self.llm_client is not None:
                try:
                    points = self._llm_points(facts, retrieval, evidence_ids)
                except Exception as exc:
                    if self.fail_on_llm_error:
                        raise RuntimeError(f"Review LLM 调用失败，已停止：{exc}") from exc
                    points = None
                if not points and self.fail_on_llm_error:
                    raise RuntimeError("Review LLM 返回结果为空或格式无效，已停止")
            if not points:
                points = self._template_points(facts, retrieval, evidence_ids)

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
            "prompt_version": self.prompt_version,
            "evidence_sufficient": len(retrieval.items) > 0,
        }
