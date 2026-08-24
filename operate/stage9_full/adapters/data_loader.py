"""读取已验收的学生1/2/3数据。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from adapters import schema_adapter


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _read_text(path).splitlines() if line.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_read_text(path))))


def load_profiles(data: dict[str, Path]) -> dict[tuple[str, str], dict[str, Any]]:
    prof_rows = _read_csv(data["profiles_train_val"])
    supp_rows = _read_csv(data["profile_supplement"])
    supp_by_key = {(r["sample_id"], r["quarter"]): r for r in supp_rows}
    cards: dict[tuple[str, str], dict[str, Any]] = {}
    for row in prof_rows:
        key = (row["sample_id"], row["quarter"])
        cards[key] = schema_adapter.row_to_profile_card(row, supp_by_key.get(key))
    return cards


def load_benchmark_cases(data: dict[str, Path]) -> list[dict[str, Any]]:
    return _read_jsonl(data["benchmark_cases"])


def load_red_team_cases(data: dict[str, Path]) -> list[dict[str, Any]]:
    return _read_jsonl(data["red_team_cases"])


def load_gold(data: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {r["case_id"]: r for r in _read_jsonl(data["gold"])}


def load_manifest(data: dict[str, Path]) -> dict[str, Any]:
    return json.loads(_read_text(data["manifest"]))
