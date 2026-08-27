"""独立语义审查模块（Semantic Audit Agent）——阶段8新增。

与确定性程序核对器（AuditAgent）分离：
- provider=qwen 时调用独立 LLM（使用 src/llm/client.py 统一接口），
  判断"每条陈述是否真的被已有画像事实和法规证据支持"；
- provider=dummy 时使用独立的规则回退（independent_rule），
  逐条核对引用存在性与数字一致性，不依赖 AuditAgent 的实现。

输出每条原子陈述的 PASS / DEFER / REJECT 及原因；只审查，不修改原始数据。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import RetrievalResult
from src.llm.client import LLMClient


_FORBIDDEN_WORDS = ("违反了", "违法", "处罚", "必然", "必定", "事故必然", "应当罚款")


class SemanticAuditAgent:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        use_llm: bool = False,
        prompt_path: str | None = None,
        model: str = "semantic-audit-v0.1",
        prompt_version: str = "semantic_audit_v1",
    ):
        self.llm_client = llm_client
        self.use_llm = use_llm
        self.prompt_path = prompt_path
        self.model = model
        self.prompt_version = prompt_version

    def _system_prompt(self) -> str:
        if self.prompt_path:
            path = Path(self.prompt_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
        return (
            "你是独立的内容审查员。请判断每条陈述是否被给定画像事实和法规证据支持，"
            "输出 JSON：{\"claims\":[{\"claim_id\":\"...\",\"verdict\":\"PASS|DEFER|REJECT\",\"reason\":\"...\"}]}。"
        )

    # ---------------------------------------------------------------- 规则回退
    def _rule_fallback(
        self,
        claims: list[dict[str, Any]],
        profile_facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
    ) -> list[dict[str, str]]:
        known_fact_fields = {f["field"] for f in profile_facts}
        fact_values = {f["field"]: f["value"] for f in profile_facts}
        known_evidence = {item.evidence_id for item in retrieval.items}
        known_standards = {item.standard_number.lower() for item in retrieval.items}

        results: list[dict[str, str]] = []
        for claim in claims:
            statement = str(claim.get("statement_zh", ""))
            refs = list(claim.get("evidence_refs", []))
            profile_refs = [r for r in refs if r.startswith("profile:")]
            regulation_refs = [r for r in refs if r.startswith("regulation:")]

            reasons: list[str] = []

            # 引用必须真实存在
            missing_profile = [r for r in profile_refs if r.split(":", 1)[1] not in known_fact_fields]
            missing_regulation = [r for r in regulation_refs if r not in known_evidence]
            if missing_profile:
                reasons.append(f"引用了不存在的画像字段：{missing_profile}")
            if missing_regulation:
                reasons.append(f"引用了不存在的法规证据：{missing_regulation}")

            # 数字与画像字段一致
            for ref in profile_refs:
                field = ref.split(":", 1)[1]
                value = fact_values.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    nums = [int(x) for x in re.findall(r"\d+", statement)]
                    if nums and int(value) not in nums:
                        reasons.append(f"陈述数字与画像字段 {field}={value} 不一致")

            # 陈述中出现的标准编号必须来自检索证据
            std_pattern = re.compile(r"\b(?:1910|1926)\.\d{1,4}(?:\([a-z0-9]+\))*\b", re.IGNORECASE)
            found_stds = {m.group(0).lower() for m in std_pattern.finditer(statement)}
            unknown_stds = sorted(found_stds - known_standards)
            if unknown_stds:
                reasons.append(f"陈述出现未在检索证据中的标准编号：{unknown_stds}")

            # 独立禁止性表达清单（与确定性核对器分离的最小集合）
            lowered = statement.lower()
            forbidden_hits = [w for w in _FORBIDDEN_WORDS if w in lowered]
            if forbidden_hits:
                reasons.append(f"命中禁止性表达：{forbidden_hits}")

            if reasons and any("禁止性表达" in r for r in reasons):
                verdict = "REJECT"
            elif reasons and any("不一致" in r or "未在检索证据" in r for r in reasons):
                verdict = "REJECT"
            elif reasons:
                verdict = "DEFER"
            elif not refs:
                verdict = "DEFER"
                reasons.append("陈述没有任何画像或法规证据引用，无法确认支持")
            else:
                verdict = "PASS"
                reasons.append("陈述可被画像事实和/或法规证据支持")
            results.append({"claim_id": claim["claim_id"], "verdict": verdict, "reason": "；".join(reasons)})
        return results

    # ---------------------------------------------------------------- LLM 路径
    def _llm_review(
        self,
        claims: list[dict[str, Any]],
        profile_facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
    ) -> list[dict[str, str]]:
        # Stage9 输入预算：证据正文发送前压缩（见 src/common/prompt_budget.py）。
        from src.common.prompt_budget import compact_evidence, compact_facts, enforce_input_budget

        user_payload = {
            "claims": claims,
            "profile_facts": compact_facts(profile_facts),
            "evidence": compact_evidence(retrieval.items),
        }
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        enforce_input_budget(messages)
        text = self.llm_client.generate(messages)
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        data = json.loads(text)
        raw = data.get("claims") or []
        results: list[dict[str, str]] = []
        for item in raw[: len(claims)]:
            verdict = str(item.get("verdict", "DEFER")).upper()
            if verdict not in ("PASS", "DEFER", "REJECT"):
                verdict = "DEFER"
            results.append(
                {
                    "claim_id": str(item.get("claim_id", "")),
                    "verdict": verdict,
                    "reason": str(item.get("reason", "")),
                }
            )
        # 对齐缺失的 claim（LLM 漏判时按 DEFER 失败关闭）
        by_id = {r["claim_id"]: r for r in results}
        aligned: list[dict[str, str]] = []
        for c in claims:
            aligned.append(
                by_id.get(
                    c["claim_id"],
                    {"claim_id": c["claim_id"], "verdict": "DEFER", "reason": "语义审查未返回该陈述结果，按 DEFER 处理"},
                )
            )
        return aligned

    def run(
        self,
        draft: dict[str, Any],
        profile_facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        audit_result: Any = None,
    ) -> dict[str, Any]:
        claims = draft.get("evidence_ledger", [])
        if self.use_llm and self.llm_client is not None:
            per_claim = self._llm_review(claims, profile_facts, retrieval)
            provider = "qwen"
        else:
            per_claim = self._rule_fallback(claims, profile_facts, retrieval)
            provider = "independent_rule"

        n_pass = sum(1 for c in per_claim if c["verdict"] == "PASS")
        n_defer = sum(1 for c in per_claim if c["verdict"] == "DEFER")
        n_reject = sum(1 for c in per_claim if c["verdict"] == "REJECT")
        if n_reject > 0:
            overall = "REJECT"
        elif n_defer > 0:
            overall = "DEFER"
        else:
            overall = "PASS"
        return {
            "sample_id": draft.get("sample_id"),
            "provider": provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "llm_used": self.use_llm and self.llm_client is not None,
            "llm_source": provider,
            "per_claim": per_claim,
            "aggregate": {"n_pass": n_pass, "n_defer": n_defer, "n_reject": n_reject},
            "overall_verdict": overall,
        }
