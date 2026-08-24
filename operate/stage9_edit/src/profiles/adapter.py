"""画像 CSV -> 画像卡适配器。

解决的问题（对应交接报告第4节的修改建议）：
1. cutoff_date -> ranking_cutoff
2. context_naics_group -> industry_group（G1—G4 + UNKNOWN）
3. 空字符串 -> null（days_since_*）
4. '0'/'1' 字符串 -> 布尔（质量标记）
5. 只保留白名单允许的字段，丢弃 label/split/future_* 等禁止字段
6. 统一为 UTF-8 输出
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


BOOL_FLAGS = [
    "no_history_flag",
    "no_positive_history_flag",
    "missing_last_inspection_flag",
    "missing_last_positive_flag",
    "entity_match_uncertain_flag",
    "insufficient_evidence_flag",
]
NULLABLE_NUM = ["days_since_last_inspection", "days_since_last_positive"]
INT_FIELDS = [
    "quarter_number",
    "history_inspections",
    "history_positive_inspections",
    "inspections_365d",
    "inspections_730d",
    "positives_365d",
    "positives_730d",
]
FLOAT_FIELDS = ["smoothed_positive_rate", "decayed_inspections", "decayed_positives"]

NAICS_TO_GROUP = {
    "221122": "G1",
    "2211_other": "G2",
    "237130": "G3",
    "238210": "G4",
}

# 原始字段 -> schema 字段名（其余同名字段保持原样）
RAW_TO_SCHEMA = {
    "cutoff_date": "ranking_cutoff",
    "context_naics_group": "industry_group",
}

# 卡片元数据，不经过白名单过滤
META_RAW_FIELDS = {"sample_id", "quarter", "cutoff_date", "profile_version", "context_naics_group"}


class ProfileAdapter:
    def __init__(self, whitelist_path: Path | str | None = None):
        self.allow_raw: set[str] | None = None
        if whitelist_path is not None:
            data = json.loads(Path(whitelist_path).read_text(encoding="utf-8"))
            if "allow_read_fields" in data:
                self.allow_raw = {item["field"] for item in data["allow_read_fields"]}
            elif "allowed_fields" in data:
                self.allow_raw = set(data["allowed_fields"])

    @staticmethod
    def _coerce(field: str, raw: str) -> Any:
        if field in BOOL_FLAGS:
            return raw in ("1", "True", "true", "TRUE", "Y", "yes")
        if field in NULLABLE_NUM:
            if raw in ("", None):
                return None
            return float(raw)
        if field in INT_FIELDS:
            if raw in ("", None):
                return 0
            return int(float(raw))
        if field in FLOAT_FIELDS:
            if raw in ("", None):
                return 0.0
            return float(raw)
        return raw

    def row_to_card(self, row: dict[str, str]) -> dict[str, Any]:
        cutoff = (row.get("cutoff_date") or "").strip()
        card: dict[str, Any] = {
            "sample_id": row["sample_id"],
            "quarter": row["quarter"],
            "ranking_cutoff": cutoff[:10],
            "profile_version": row.get("profile_version", "") or "",
            "industry_group": NAICS_TO_GROUP.get(row.get("context_naics_group", ""), "UNKNOWN"),
            "jurisdiction_context": row.get("jurisdiction_context") or None,
        }

        allow = self.allow_raw or set(row.keys())
        for raw_field in row:
            if raw_field in META_RAW_FIELDS:
                continue
            if self.allow_raw is not None and raw_field not in allow:
                continue  # 白名单外字段不进入画像卡
            schema_field = RAW_TO_SCHEMA.get(raw_field, raw_field)
            if schema_field in card:
                continue
            card[schema_field] = self._coerce(schema_field, row[raw_field])
        return card


def adapt_csv(csv_path: Path | str, whitelist_path: Path | str | None) -> list[dict[str, Any]]:
    adapter = ProfileAdapter(whitelist_path)
    cards: list[dict[str, Any]] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            cards.append(adapter.row_to_card(row))
    return cards


def write_cards_jsonl(cards: list[dict[str, Any]], out_path: Path | str) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    return out
