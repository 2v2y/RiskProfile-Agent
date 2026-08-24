"""阶段九评价指标（只读 Ground Truth，只比较，不改任何数据）。"""

from __future__ import annotations

from typing import Any


def _facts_dict(output: dict[str, Any]) -> dict[str, Any]:
    return {f["field"]: f["value"] for f in output.get("profile_facts", []) if isinstance(f, dict)}


def _claims(output: dict[str, Any]) -> list[dict[str, Any]]:
    draft = output.get("draft_review") or {}
    return draft.get("evidence_ledger") or []


def _retrieved_ids(output: dict[str, Any]) -> set[str]:
    return {i.get("evidence_id", "") for i in (output.get("retrieval") or {}).get("items", [])}


def _cited_regulation_refs(output: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for c in _claims(output):
        for r in c.get("evidence_refs", []):
            if str(r).startswith("regulation:"):
                refs.append(str(r))
    return refs


def _gold_evidence_standards(gold: dict[str, Any]) -> set[str]:
    return {
        str(r.get("standard", ""))
        for r in gold.get("gold_regulation_document_ids") or []
        if r.get("evidence_available") is True
    }


def numeric_accuracy(output: dict[str, Any], gold: dict[str, Any], tol: float = 0.001) -> dict[str, Any]:
    expected = gold.get("expected_profile_facts_subset") or {}
    facts = _facts_dict(output)
    checked = 0
    ok = 0
    for field, exp in expected.items():
        if field not in facts or not isinstance(exp, (int, float)) or isinstance(exp, bool):
            continue
        got = facts[field]
        if isinstance(got, (int, float)) and not isinstance(got, bool):
            checked += 1
            if abs(float(got) - float(exp)) <= tol:
                ok += 1
    return {"checked": checked, "correct": ok, "value": ok / checked if checked else 1.0}


def citation_validity(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    refs = _cited_regulation_refs(output)
    retrieved = _retrieved_ids(output)
    invalid = [r for r in refs if r not in retrieved]
    return {
        "n_refs": len(refs),
        "n_invalid": len(invalid),
        "value": 1.0 - len(invalid) / len(refs) if refs else 1.0,
    }


def citation_correctness(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    gold_std = _gold_evidence_standards(gold)
    cited_std: set[str] = set()
    for i in (output.get("retrieval") or {}).get("items", []):
        if i.get("standard_number"):
            cited_std.add(str(i["standard_number"]))
    hit = len(cited_std & gold_std)
    return {
        "gold_standards": len(gold_std),
        "cited_standards": len(cited_std),
        "hit": hit,
        "value": hit / len(gold_std) if gold_std else 1.0,
    }


def evidence_support(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    claims = _claims(output)
    supported = sum(1 for c in claims if c.get("evidence_refs"))
    return {"n_claims": len(claims), "supported": supported,
            "value": supported / len(claims) if claims else 1.0}


def unsupported_claim(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    claims = _claims(output)
    unsupported = sum(1 for c in claims if not c.get("evidence_refs"))
    return {"n_claims": len(claims), "unsupported": unsupported,
            "value": unsupported / len(claims) if claims else 0.0}


def traceability(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    claims = _claims(output)
    fact_fields = set(_facts_dict(output).keys())
    retrieved = _retrieved_ids(output)
    traceable = 0
    for c in claims:
        refs = c.get("evidence_refs") or []
        ok = True
        for r in refs:
            r = str(r)
            if r.startswith("profile:"):
                if r.split(":", 1)[1] not in fact_fields:
                    ok = False
            elif r.startswith("regulation:"):
                if r not in retrieved:
                    ok = False
        if ok:
            traceable += 1
    return {"n_claims": len(claims), "traceable": traceable,
            "value": traceable / len(claims) if claims else 1.0}


def safe_refusal(output: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    expected = gold.get("expected_safe_defer_or_pass")
    verdict = output.get("final_verdict")
    correct = (expected is True and verdict in ("DEFER", "REJECT")) or (
        expected is None and verdict == "PASS"
    )
    return {"expected": expected, "verdict": verdict, "value": 1.0 if correct else 0.0}


METRICS = {
    "numeric_accuracy": numeric_accuracy,
    "citation_validity": citation_validity,
    "citation_correctness": citation_correctness,
    "evidence_support": evidence_support,
    "unsupported_claim": unsupported_claim,
    "traceability": traceability,
    "safe_refusal": safe_refusal,
}


def compute_sample_metrics(output: dict[str, Any], gold: dict[str, Any],
                           tol: float = 0.001) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sample_id": output.get("sample_id"),
        "method": output.get("method"),
        "final_verdict": output.get("final_verdict"),
    }
    for name, fn in METRICS.items():
        if name == "numeric_accuracy":
            res = fn(output, gold, tol)
        else:
            res = fn(output, gold)
        row[name] = res["value"]
        row[f"{name}_detail"] = {k: v for k, v in res.items() if k != "value"}
    return row


def aggregate(rows: list[dict[str, Any]], metric_names: list[str]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    agg: dict[str, Any] = {"n": len(rows)}
    verdicts: dict[str, int] = {}
    for r in rows:
        v = str(r.get("final_verdict", "UNKNOWN"))
        verdicts[v] = verdicts.get(v, 0) + 1
    agg["final_verdict_distribution"] = verdicts
    for m in metric_names:
        vals = [float(r[m]) for r in rows if m in r]
        if vals:
            agg[m] = {
                "mean": round(sum(vals) / len(vals), 6),
                "n_available": len(vals),
            }
    return agg
