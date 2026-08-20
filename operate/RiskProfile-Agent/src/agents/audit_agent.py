"""内容审查模块（Audit Agent）——确定性核对器（阶段1）。

把报告拆分为原子陈述，逐条检查：
- 数字是否与画像一致（number_consistency）
- 标准和条款是否存在（citation_exists）
- 引用片段是否支持陈述（evidence_supports）
- 是否出现违法认定、事故必然性或处罚意见（forbidden_claim）
- 是否隐瞒关键缺失信息（missing_info_hidden）
- 是否使用未来信息（future_info_used）
- 是否中美法规混用（regulation_mix）

每条陈述输出 PASS / DEFER / REJECT 及原因。阶段8再加入独立语义审查（LLM）。
"""

from __future__ import annotations

import re
from typing import Any

from src.common.pydantic_schemas import AuditCheck, AuditClaim, AuditResult, RetrievalResult


_UNCERTAINTY_MARKERS = ("不确定", "可能", "缺少", "缺乏", "建议", "需人工", "未检索到", "未知")


class AuditAgent:
    def __init__(self, forbidden_patterns: list[str], max_rounds: int = 2):
        self.forbidden_patterns = [p.lower() for p in forbidden_patterns]
        self.max_rounds = max_rounds

    def _fact_value(self, profile_facts: list[dict[str, Any]], field: str) -> Any:
        for fact in profile_facts:
            if fact["field"] == field:
                return fact["value"]
        return None

    def _check_claim(
        self,
        claim: dict[str, Any],
        profile_facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        profile: dict[str, Any],
    ) -> AuditClaim:
        statement = claim["statement_zh"]
        refs = list(claim.get("evidence_refs", []))
        checks: list[AuditCheck] = []

        # 1) 数字一致性
        number_ok = True
        number_detail = "陈述中的数字与画像字段一致"
        for ref in refs:
            if ref.startswith("profile:"):
                field = ref.split(":", 1)[1]
                value = self._fact_value(profile_facts, field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    nums = [int(x) for x in re.findall(r"\d+", statement)]
                    # 只有陈述确实写了数字时才核对：写了但写错 -> 数字错误；
                    # 没写数字 -> 不构成数字错误（字段遗漏由 Evaluation 的覆盖率指标衡量）。
                    if nums and int(value) not in nums:
                        number_ok = False
                        number_detail = f"陈述数字与画像字段 {field}={value} 不一致（陈述含 {nums}）"
                        break
        checks.append(AuditCheck(check="number_consistency", passed=number_ok, detail=number_detail))

        # 2) 引用存在性
        known_docs = {item.document_id for item in retrieval.items}
        missing_refs = [
            ref for ref in refs
            if ref.startswith("regulation:") and ref.split(":", 1)[1].split("#", 1)[0] not in known_docs
        ]
        citation_ok = not missing_refs
        checks.append(
            AuditCheck(
                check="citation_exists",
                passed=citation_ok,
                detail="全部法规引用可回溯" if citation_ok else f"以下引用未在检索结果中：{missing_refs}",
            )
        )

        # 3) 依据充分性
        has_evidence = len(refs) > 0
        checks.append(
            AuditCheck(
                check="evidence_supports",
                passed=has_evidence,
                detail="陈述有画像或法规依据" if has_evidence else "陈述没有任何证据引用（无依据内容）",
            )
        )

        # 4) 禁止性表述
        lowered = statement.lower()
        forbidden_hits = [p for p in self.forbidden_patterns if p in lowered]
        forbidden_ok = not forbidden_hits
        checks.append(
            AuditCheck(
                check="forbidden_claim",
                passed=forbidden_ok,
                detail="未发现违法认定/处罚/事故必然性表述" if forbidden_ok else f"命中禁止性表述：{forbidden_hits}",
            )
        )

        # 5) 缺失信息是否被隐瞒
        flags = [k for k in ("insufficient_evidence_flag", "no_history_flag") if profile.get(k)]
        hidden = bool(flags) and not any(m in statement for m in _UNCERTAINTY_MARKERS)
        checks.append(
            AuditCheck(
                check="missing_info_hidden",
                passed=not hidden,
                detail="缺失信息已如实说明" if not hidden else f"存在缺失标记 {flags} 但陈述未说明不确定性",
            )
        )

        # 6) 未来信息
        future_used = ("未来" in statement) or ("future" in lowered)
        checks.append(
            AuditCheck(
                check="future_info_used",
                passed=not future_used,
                detail="未发现未来信息" if not future_used else "陈述疑似使用未来信息（目标为0）",
            )
        )

        # 7) 中美法规混用
        mixed = ("osha" in lowered) and any(m in statement for m in ("GB", "中国", "国标"))
        checks.append(
            AuditCheck(
                check="regulation_mix",
                passed=not mixed,
                detail="未发现中美法规混用" if not mixed else "同一陈述混用 OSHA 与中国法规表述",
            )
        )

        hard_failures = {
            "number_consistency",
            "citation_exists",
            "forbidden_claim",
            "future_info_used",
            "regulation_mix",
        }
        failed_hard = [c for c in checks if c.check in hard_failures and not c.passed]
        failed_soft = [c for c in checks if c.check in {"evidence_supports", "missing_info_hidden"} and not c.passed]

        if failed_hard:
            verdict = "REJECT"
        elif failed_soft:
            verdict = "DEFER"
        else:
            verdict = "PASS"

        reasons = [
            c.detail for c in checks if not c.passed
        ]
        return AuditClaim(
            claim_id=claim["claim_id"],
            statement_zh=statement,
            checks=checks,
            verdict=verdict,
            reasons=reasons,
            auto_fix_applied="none",
        )

    def run(
        self,
        draft: dict[str, Any],
        profile_facts: list[dict[str, Any]],
        retrieval: RetrievalResult,
        profile: dict[str, Any],
    ) -> AuditResult:
        claim_results = [
            self._check_claim(c, profile_facts, retrieval, profile)
            for c in draft["evidence_ledger"]
        ]
        n_pass = sum(1 for c in claim_results if c.verdict == "PASS")
        n_defer = sum(1 for c in claim_results if c.verdict == "DEFER")
        n_reject = sum(1 for c in claim_results if c.verdict == "REJECT")

        if n_reject > 0:
            overall = "REJECT"
        elif n_defer > 0:
            overall = "DEFER"
        else:
            overall = "PASS"

        return AuditResult(
            sample_id=draft["sample_id"],
            audit_round=1,
            max_rounds=self.max_rounds,
            claims=claim_results,
            aggregate={
                "n_pass": n_pass,
                "n_defer": n_defer,
                "n_reject": n_reject,
                "first_attempt_pass": n_defer == 0 and n_reject == 0,
            },
            overall_verdict=overall,
        )
