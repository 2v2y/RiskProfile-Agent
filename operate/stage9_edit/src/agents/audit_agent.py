"""内容审查模块（Audit Agent）——确定性程序核对器（阶段8）。

把报告拆分为原子陈述，逐条检查（Python 确定性规则，不使用 LLM）：
1. number_consistency：数字是否与画像来源一致；样本ID是否一致；
2. citation_exists：法规引用是否真实存在；陈述中出现的OSHA标准编号是否来自检索证据/画像输入；
3. evidence_supports：陈述是否有画像或法规依据；
4. forbidden_claim：是否出现违法认定、处罚建议、事故必然性等禁止性表达（规则文件外置）；
5. missing_info_hidden：是否隐瞒关键缺失信息；
6. future_info_used：是否使用未来字段/未来日期；
7. regulation_mix：是否混用美国/中国法规。

每条陈述输出 PASS / DEFER / REJECT 及原因。
独立语义审查由 src/agents/semantic_audit_agent.py 负责，与本模块分离。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import AuditCheck, AuditClaim, AuditResult, RetrievalResult


_UNCERTAINTY_MARKERS = ("不确定", "可能", "缺少", "缺乏", "建议", "需人工", "未检索到", "未知")


def load_forbidden_patterns(path: str | None) -> list[str]:
    """从 forbidden_claim_rules.yaml 读取禁止性表达模式；文件缺失/解析失败时返回空列表。"""
    if not path:
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    patterns: list[str] = []
    for rule in data.get("claim_level_forbidden", []) or []:
        patterns.extend(str(p) for p in rule.get("patterns", []) or [])
    return patterns


class AuditAgent:
    def __init__(
        self,
        forbidden_patterns: list[str],
        max_rounds: int = 2,
        forbidden_rules_path: str | None = None,
    ):
        merged = list(forbidden_patterns or []) + load_forbidden_patterns(forbidden_rules_path)
        self.forbidden_patterns: list[str] = []
        seen: set[str] = set()
        for p in merged:
            low = p.lower()
            if low not in seen:
                seen.add(low)
                self.forbidden_patterns.append(low)
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

        # 1) 数字一致性 + ID 一致性（并入 number_consistency，保持 schema 枚举不变）
        number_ok = True
        number_detail = "陈述中的数字与画像字段一致"
        for ref in refs:
            if ref.startswith("profile:"):
                field = ref.split(":", 1)[1]
                value = self._fact_value(profile_facts, field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    nums = [int(x) for x in re.findall(r"\d+", statement)]
                    # 陈述写了数字才核对：写错 -> 数字错误；没写 -> 不构成数字错误
                    if nums and int(value) not in nums:
                        number_ok = False
                        number_detail = f"陈述数字与画像字段 {field}={value} 不一致（陈述含 {nums}）"
                        break
        sample_id = profile.get("sample_id", "")
        ids_in_statement = [x for x in re.findall(r"\bSAMPLE[_\-\w]*\b", statement, re.IGNORECASE)]
        mismatched_ids = [x for x in ids_in_statement if x != sample_id]
        if mismatched_ids:
            number_ok = False
            number_detail = f"陈述中的样本ID与画像不一致：{mismatched_ids}（画像 sample_id={sample_id}）"
        checks.append(AuditCheck(check="number_consistency", passed=number_ok, detail=number_detail))

        # 2) 引用存在性 + 标准编号溯源（并入 citation_exists）
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

        std_pattern = re.compile(r"\b(?:1910|1926)\.\d{1,4}(?:\([a-z0-9]+\))*\b", re.IGNORECASE)
        found_stds = {m.group(0).lower() for m in std_pattern.finditer(statement)}
        known_stds = {item.standard_number.lower() for item in retrieval.items}
        known_stds.update(str(s).lower() for s in (profile.get("historical_standard_codes") or []))
        unknown_stds = sorted(found_stds - known_stds)
        checks.append(
            AuditCheck(
                check="citation_exists",
                passed=not unknown_stds,
                detail=(
                    "陈述中的标准编号均可回溯到检索证据或画像输入"
                    if not unknown_stds
                    else f"陈述出现未在检索证据/画像输入中的标准编号：{unknown_stds}"
                ),
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

        # 4) 禁止性表述（规则来自 forbidden_claim_rules.yaml + config 兜底）
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

        # 6) 未来信息（含未来日期检查）
        future_used = ("未来" in statement) or ("future" in lowered)
        date_pattern = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")
        cutoff = profile.get("ranking_cutoff")
        try:
            cutoff_date = date.fromisoformat(str(cutoff)) if cutoff else None
        except ValueError:
            cutoff_date = None
        if cutoff_date is not None:
            for m in date_pattern.finditer(statement):
                try:
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
                if d > cutoff_date:
                    future_used = True
                    break
        checks.append(
            AuditCheck(
                check="future_info_used",
                passed=not future_used,
                detail="未发现未来信息" if not future_used else "陈述使用未来字段或画像截止日之后的日期（目标为0）",
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

        reasons = [c.detail for c in checks if not c.passed]
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
