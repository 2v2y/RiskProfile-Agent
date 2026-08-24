"""阶段9数据加载：把学生1画像、风险补充字段整理成 Agent 可读的画像卡。

说明：
- profiles_train_val.csv 提供基础画像和 split；
- profile_supplement_8fields.csv 提供 R1-R9、历史标准编号、risk_score 等补充字段；
- 两者按 (sample_id, quarter) 合并。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.experiments.paths import STAGE9_ROOT
from src.experiments.retrieval_adapter import convert_standard


NAICS_TO_GROUP = {
    "221122": "G1",
    "2211_other": "G2",
    "237130": "G3",
    "238210": "G4",
}


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "y", "yes")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(str(value))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_json_dict(value: Any) -> dict[str, int]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _standard_codes(raw: str) -> list[str]:
    codes: list[str] = []
    for part in str(raw or "").split(";"):
        part = part.strip()
        if not part:
            continue
        conv = convert_standard(part)
        codes.append(conv or part)
    return codes


def build_profile_card(row: dict[str, str], supplement: dict[str, str] | None) -> dict[str, Any] | None:
    history_inspections = int(_num(row.get("history_inspections"), 0))
    history_positive = int(_num(row.get("history_positive_inspections"), 0))
    days_last_insp = row.get("days_since_last_inspection")
    days_last_pos = row.get("days_since_last_positive")
    no_history_flag = history_inspections == 0
    no_positive_flag = history_inspections > 0 and history_positive == 0
    missing_last_insp_flag = days_last_insp in ("", None)
    missing_last_pos_flag = days_last_pos in ("", None)
    insufficient_flag = no_history_flag or missing_last_insp_flag or missing_last_pos_flag

    card: dict[str, Any] = {
        "sample_id": row["sample_id"],
        "quarter": row["quarter"],
        "ranking_cutoff": str(row.get("cutoff_date", ""))[:10],
        "profile_version": "student1-profile-v1",
        "industry_group": NAICS_TO_GROUP.get(row.get("context_naics_group", ""), "UNKNOWN"),
        "jurisdiction_context": row.get("context_site_state") or row.get("candidate_site_state") or "UNKNOWN",
        "quarter_number": int(_num(row.get("quarter_number"), 1)),
        "history_inspections": history_inspections,
        "history_positive_inspections": history_positive,
        "smoothed_positive_rate": _num(row.get("smoothed_positive_rate"), 0.5),
        "days_since_last_inspection": _num(days_last_insp) if days_last_insp not in ("", None) else None,
        "days_since_last_positive": _num(days_last_pos) if days_last_pos not in ("", None) else None,
        "inspections_365d": int(_num(row.get("inspections_365d"), 0)),
        "positives_365d": int(_num(row.get("positives_365d"), 0)),
        "inspections_730d": int(_num(row.get("inspections_730d"), 0)),
        "positives_730d": int(_num(row.get("positives_730d"), 0)),
        "decayed_inspections": _num(row.get("decayed_inspections"), 0.0),
        "decayed_positives": _num(row.get("decayed_positives"), 0.0),
        "no_history_flag": no_history_flag,
        "no_positive_history_flag": no_positive_flag,
        "missing_last_inspection_flag": missing_last_insp_flag,
        "missing_last_positive_flag": missing_last_pos_flag,
        "entity_match_uncertain_flag": False,
        "insufficient_evidence_flag": insufficient_flag,
    }

    if supplement:
        card["historical_standard_codes"] = _standard_codes(supplement.get("historical_standard_codes", ""))
        card["historical_risk_categories"] = _parse_json_list(supplement.get("historical_risk_categories", ""))
        card["risk_category_counts"] = _parse_json_dict(supplement.get("risk_category_counts", ""))
        card["risk_category_unmapped_rate"] = _num(supplement.get("risk_category_unmapped_rate"), 0.0)
        score_raw = supplement.get("risk_score", "")
        card["risk_score"] = _num(score_raw) if score_raw not in ("", None) else None
        percentile_raw = supplement.get("risk_percentile", "")
        # 学生1交付的 risk_percentile 为 0-100 百分位，Schema 要求 0-1。
        card["risk_percentile"] = round(_num(percentile_raw) / 100.0, 6) if percentile_raw not in ("", None) else None
        card["model_version"] = supplement.get("model_version", "")
        card["score_evidence"] = supplement.get("score_evidence", "")

    return card


def load_profiles_with_risk(
    n: int | None = None,
    split: str | None = "validation",
    root: Path | str | None = None,
) -> list[dict[str, Any]]:
    root = Path(root) if root else STAGE9_ROOT
    base_path = root / "data" / "02_train_validation" / "profiles_train_val.csv"
    supp_path = root / "data" / "02_train_validation" / "profile_supplement_8fields.csv"

    supplement_by_key: dict[tuple[str, str], dict[str, str]] = {}
    with open(supp_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            supplement_by_key[(row["sample_id"], row["quarter"])] = row

    cards: list[dict[str, Any]] = []
    with open(base_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if split is not None and row.get("split") != split:
                continue
            card = build_profile_card(row, supplement_by_key.get((row["sample_id"], row["quarter"])))
            if card is not None:
                cards.append(card)
            if n is not None and len(cards) >= n:
                break
    return cards


def load_jsonl(path: Path | str, n: int | None = None) -> list[dict[str, Any]]:
    path = Path(path)
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
            if n is not None and len(items) >= n:
                break
    return items
