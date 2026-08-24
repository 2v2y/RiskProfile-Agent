"""错误分析：把每样本每方法归类到错误类型（保留样本ID）。"""

from __future__ import annotations

from typing import Any

ERROR_TYPES = [
    "NUMERIC_ERROR",
    "CITATION_ERROR",
    "EVIDENCE_UNSUPPORTED",
    "UNSUPPORTED_CLAIM",
    "OUT_OF_SCOPE",
    "REFUSAL_ERROR",
    "OTHER",
]


def classify(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("numeric_accuracy", 1.0) < 1.0:
        errors.append("NUMERIC_ERROR")
    if row.get("citation_validity", 1.0) < 1.0 or row.get("citation_correctness", 1.0) < 1.0:
        errors.append("CITATION_ERROR")
    if row.get("traceability", 1.0) < 1.0:
        errors.append("EVIDENCE_UNSUPPORTED")
    if row.get("unsupported_claim", 0.0) > 0.0:
        errors.append("UNSUPPORTED_CLAIM")
    if row.get("safe_refusal", 1.0) < 1.0:
        errors.append("REFUSAL_ERROR")
    # OUT_OF_SCOPE 需要语义判断，占位为 0，正式接入 Qwen 后由语义审查/人工补标。
    return errors or ["NONE"]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        method = str(r.get("method", "?"))
        bucket = counts.setdefault(method, {t: 0 for t in ERROR_TYPES})
        bucket.setdefault("NONE", 0)
        for e in classify(r):
            bucket[e] = bucket.get(e, 0) + 1
    total_by_method = {m: sum(v.values()) for m, v in counts.items()}
    rate: dict[str, dict[str, float]] = {}
    for m, v in counts.items():
        n = total_by_method[m] or 1
        rate[m] = {t: round(c / n, 6) for t, c in v.items()}
    return {"counts": counts, "rates": rate, "error_samples": [
        {"method": r["method"], "sample_id": r["sample_id"], "errors": classify(r)}
        for r in rows if "NONE" not in classify(r)
    ]}
