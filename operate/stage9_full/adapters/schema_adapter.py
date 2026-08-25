"""学生1画像CSV -> ProfileCard 的字段适配（只做映射与类型转换，不改已验收数据）。

标准编号统一走 canonical_standard 层（Canonical Standard = OSHA 官方引用格式，
见 docs/standard_consistency_analysis.md），保证画像、映射、检索三层口径一致。
"""

from __future__ import annotations

import json
from typing import Any

from adapters import canonical_standard

NAICS_TO_GROUP = {
    "221122": "G1",
    "2211_other": "G2",
    "237130": "G3",
    "238210": "G4",
}


def convert_standard(raw: str) -> str | None:
    """标准编号统一转换入口（委托 canonical_standard.canonicalize）。

    兼容旧行为：DOL 原值 ``19260651 J02`` → ``1926.651``；新增统一：映射键
    ``1926.0651`` → ``1926.651``；非联邦编号原样保留。
    """
    return canonical_standard.canonicalize(raw)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(str(value))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _json_dict(value: Any) -> dict[str, int]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def row_to_profile_card(prof: dict[str, str], supp: dict[str, str] | None) -> dict[str, Any]:
    history = int(_num(prof.get("history_inspections"), 0))
    positive = int(_num(prof.get("history_positive_inspections"), 0))
    days_insp = (prof.get("days_since_last_inspection") or "").strip()
    days_pos = (prof.get("days_since_last_positive") or "").strip()

    no_history = history == 0
    no_positive = history > 0 and positive == 0
    missing_insp = days_insp == ""
    missing_pos = days_pos == ""
    insufficient = no_history or missing_insp or missing_pos

    card: dict[str, Any] = {
        "sample_id": prof["sample_id"],
        "quarter": prof["quarter"],
        "ranking_cutoff": str(prof.get("cutoff_date", ""))[:10],
        "profile_version": "FREEZE_20260814_001",
        "industry_group": NAICS_TO_GROUP.get(prof.get("context_naics_group", ""), "UNKNOWN"),
        "jurisdiction_context": prof.get("context_site_state") or prof.get("candidate_site_state") or None,
        "quarter_number": int(_num(prof.get("quarter_number"), 1)),
        "history_inspections": history,
        "history_positive_inspections": positive,
        "smoothed_positive_rate": _num(prof.get("smoothed_positive_rate"), 0.5),
        "days_since_last_inspection": float(days_insp) if days_insp else None,
        "days_since_last_positive": float(days_pos) if days_pos else None,
        "inspections_365d": int(_num(prof.get("inspections_365d"), 0)),
        "positives_365d": int(_num(prof.get("positives_365d"), 0)),
        "inspections_730d": int(_num(prof.get("inspections_730d"), 0)),
        "positives_730d": int(_num(prof.get("positives_730d"), 0)),
        "decayed_inspections": _num(prof.get("decayed_inspections"), 0.0),
        "decayed_positives": _num(prof.get("decayed_positives"), 0.0),
        "no_history_flag": no_history,
        "no_positive_history_flag": no_positive,
        "missing_last_inspection_flag": missing_insp,
        "missing_last_positive_flag": missing_pos,
        "entity_match_uncertain_flag": False,
        "insufficient_evidence_flag": insufficient,
    }

    if supp:
        card["historical_standard_codes"] = [
            c.strip()
            for c in str(supp.get("historical_standard_codes", "")).split(";")
            if c.strip()
        ]
        card["historical_risk_categories"] = _json_list(supp.get("historical_risk_categories", ""))
        card["risk_category_counts"] = _json_dict(supp.get("risk_category_counts", ""))
        card["risk_category_unmapped_rate"] = _num(supp.get("risk_category_unmapped_rate"), 0.0)
        score_raw = str(supp.get("risk_score", "")).strip()
        card["risk_score"] = float(score_raw) if score_raw else None
        pct_raw = str(supp.get("risk_percentile", "")).strip()
        card["risk_percentile"] = round(float(pct_raw) / 100.0, 6) if pct_raw else None
        card["model_version"] = supp.get("model_version", "")
        card["score_evidence"] = supp.get("score_evidence", "")

    return card
