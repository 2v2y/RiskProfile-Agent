from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
import pandas as pd

from pipeline_common import DEFAULT_ROOT, ensure_dirs, fail_if_analysis_frozen, fail_if_test_opened, industry_group, load_config, normalize_activity, normalize_text, secure_restricted_tree, sha256_file, write_json


def entity_proxy(row: pd.Series, fallback: bool) -> tuple[str, str, str, str]:
    name = normalize_text(row["estab_name"])
    mail_street = normalize_text(row["mail_street"])
    mail_zip = normalize_text(row["mail_zip"])[:5]
    source = "mail"
    street, zip5 = mail_street, mail_zip
    if fallback and (not street or not zip5):
        street = normalize_text(row["site_address"])
        zip5 = normalize_text(row["site_zip"])[:5]
        source = "site_fallback"
    if not name or not street or not zip5:
        return "", source, street, zip5
    material = f"{name}|{street}|{zip5}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24], source, street, zip5


def blinded_fragment(value: Any, limit: int) -> str:
    """Return only the short normalized fragment needed for independent pair review."""
    normalized = normalize_text(value)
    if not normalized:
        return ""
    return normalized[:limit] + ("…" if len(normalized) > limit else "")


def split_name(date: pd.Timestamp, splits: dict[str, str]) -> str:
    ranges = [
        ("train", splits["train_start"], splits["train_end"]),
        ("validation", splits["validation_start"], splits["validation_end"]),
        ("embargo", splits["embargo_start"], splits["embargo_end"]),
        ("test", splits["test_start"], splits["test_end"]),
    ]
    for name, start, end in ranges:
        if pd.Timestamp(start) <= date <= pd.Timestamp(end):
            return name
    return "outside"


def validate_download_manifest(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    raw = root / "数据/00_原始数据"
    path = raw / "download_manifest.json"
    if not path.exists():
        raise RuntimeError("缺少download_manifest.json；正式流水线拒绝未核验输入。")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise RuntimeError("download_manifest.complete不是true；拒绝部分下载或残留CSV。")
    entries = {item.get("dataset"): item for item in manifest.get("datasets", [])}
    for name in ("inspection", "violation"):
        item = entries.get(name)
        data_path = raw / cfg["download"]["endpoints"][name]["output"]
        if not item or item.get("complete") is not True or not data_path.exists():
            raise RuntimeError(f"{name}的manifest或CSV不完整。")
        with data_path.open("r", encoding="utf-8", newline="") as handle:
            rows = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
        if Path(str(item.get("output"))).name != data_path.name or int(item.get("rows", -1)) != rows or item.get("sha256") != sha256_file(data_path):
            raise RuntimeError(f"{name}的路径、行数或SHA-256与manifest不一致。")
        endpoint = cfg["download"]["endpoints"][name]
        expected_request = {
            "url": f"{cfg['download']['base_url'].rstrip('/')}/get/OSHA/{endpoint['api_name']}/json",
            "limit": int(cfg["download"]["page_limit"]), "sort": "asc", "sort_by": endpoint["sort_by"],
            "filter_enabled": bool(cfg["download"].get("filter_enabled", True)),
            "filter_object": endpoint.get("filter_object"),
        }
        if item.get("request") != expected_request:
            raise RuntimeError(f"{name}的下载请求参数与当前config不一致。")
    downloaded_at = pd.to_datetime(manifest.get("downloaded_at_utc"), errors="coerce", utc=True)
    if pd.isna(downloaded_at):
        raise RuntimeError("download_manifest缺少可解析的downloaded_at_utc；无法冻结研究as-of。")
    return manifest


def _csv_column_mapping(path: Path, required: list[str], source: str) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"未找到{source}: {path}")
    header = pd.read_csv(path, nrows=0).columns
    mapping = {str(column).strip().lower(): str(column) for column in header}
    missing = sorted(set(required) - set(mapping))
    if missing:
        raise ValueError(f"{source}缺少必需字段: {missing}。程序已停止，不猜测字段。")
    return {mapping[column]: column for column in required}


def _row_sha256(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    material = frame[columns].fillna("").astype(str).agg("\x1f".join, axis=1)
    return material.map(lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())


def _append_sqlite(connection: sqlite3.Connection, table: str, frame: pd.DataFrame) -> None:
    if not frame.empty:
        frame.to_sql(table, connection, if_exists="append", index=False, chunksize=500)


def _deduplicated_frame(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
    keys: list[str],
    outer_filter_sql: str | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return pd.DataFrame(columns=columns), {"rows_in_duplicate_groups": 0, "duplicate_groups": 0, "rows_removed": 0}
    partition = ", ".join(f'"{key}"' for key in keys)
    selected = ", ".join(f'"{column}"' for column in columns)
    outer_filter = f" AND ({outer_filter_sql})" if outer_filter_sql else ""
    query = f'''SELECT {selected} FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY {partition}
            ORDER BY "_load_sort" DESC, "_row_fingerprint" DESC
        ) AS _rank
        FROM "{table}"
    ) WHERE _rank = 1{outer_filter} ORDER BY {partition}'''
    # 不强制dtype=str；否则SQLite NULL会变成字面量"None"，导致notna筛选失真。
    frame = pd.read_sql_query(query, connection)
    group_query = f'''SELECT COUNT(*) AS groups_n, COALESCE(SUM(group_n), 0) AS rows_n
        FROM (SELECT COUNT(*) AS group_n FROM "{table}" GROUP BY {partition} HAVING COUNT(*) > 1)'''
    groups_n, rows_n = connection.execute(group_query).fetchone()
    groups_n, rows_n = int(groups_n or 0), int(rows_n or 0)
    return frame, {"rows_in_duplicate_groups": rows_n, "duplicate_groups": groups_n, "rows_removed": rows_n - groups_n}


def load_target_inputs(raw: Path, cfg: dict[str, Any], sqlite_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Stream official CSVs, retain target NAICS inspections and only their violations, then deduplicate on disk."""
    chunk_rows = int(cfg.get("performance", {}).get("csv_chunk_rows", 100_000))
    inspection_required = list(cfg["schema"]["inspection_required"])
    violation_required = list(cfg["schema"]["violation_required"])
    inspection_path = raw / "osha_inspection.csv"
    violation_path = raw / "osha_violation.csv"
    inspection_mapping = _csv_column_mapping(inspection_path, inspection_required, "Inspection正式数据")
    violation_mapping = _csv_column_mapping(violation_path, violation_required, "Violation正式数据")
    stats: dict[str, Any] = {"inspection_raw_rows_scanned": 0, "inspection_rows_staged": 0, "inspection_target_latest_rows": 0, "violation_raw_rows_scanned": 0, "violation_relevant_rows_staged": 0}
    inspection_max_load = pd.NaT
    violation_max_load = pd.NaT
    connection = sqlite3.connect(sqlite_path)
    try:
        for chunk in pd.read_csv(inspection_path, dtype=str, usecols=list(inspection_mapping), chunksize=chunk_rows, low_memory=False):
            chunk = chunk.rename(columns=inspection_mapping)
            stats["inspection_raw_rows_scanned"] += len(chunk)
            load_dates = pd.to_datetime(chunk["load_dt"], errors="coerce", utc=True).dt.tz_localize(None)
            if load_dates.notna().any():
                chunk_max = load_dates.max()
                inspection_max_load = chunk_max if pd.isna(inspection_max_load) else max(inspection_max_load, chunk_max)
            # 必须先对全体Inspection按activity_nr保留最新load_dt，再判断最新行NAICS。
            # 若先筛NAICS，旧目标行业行可能在最新行已改出目标行业时被错误保留。
            chunk["naics_group"] = chunk["naics_code"].map(industry_group)
            chunk["activity_nr"] = chunk["activity_nr"].map(normalize_activity)
            if chunk["activity_nr"].eq("").any():
                raise ValueError("Inspection.activity_nr存在空值；不能猜测连接键。")
            chunk["_load_sort"] = pd.to_datetime(chunk["load_dt"], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").fillna("")
            chunk["_row_fingerprint"] = _row_sha256(chunk, inspection_required)
            _append_sqlite(connection, "inspection_stage", chunk[inspection_required + ["naics_group", "_load_sort", "_row_fingerprint"]])
            stats["inspection_rows_staged"] += len(chunk)
        if pd.isna(inspection_max_load):
            raise ValueError("Inspection.load_dt无法解析，不能确定快照日期。")
        inspections, inspection_duplicates = _deduplicated_frame(
            connection,
            "inspection_stage",
            inspection_required + ["naics_group"],
            ["activity_nr"],
            outer_filter_sql='"naics_group" IS NOT NULL',
        )
        stats["inspection_target_latest_rows"] = len(inspections)
        if inspections.empty:
            raise ValueError("按互斥行业组筛选后无Inspection记录。")
        relevant_activity = set(inspections["activity_nr"])
        for chunk in pd.read_csv(violation_path, dtype=str, usecols=list(violation_mapping), chunksize=chunk_rows, low_memory=False):
            chunk = chunk.rename(columns=violation_mapping)
            stats["violation_raw_rows_scanned"] += len(chunk)
            load_dates = pd.to_datetime(chunk["load_dt"], errors="coerce", utc=True).dt.tz_localize(None)
            if load_dates.notna().any():
                chunk_max = load_dates.max()
                violation_max_load = chunk_max if pd.isna(violation_max_load) else max(violation_max_load, chunk_max)
            chunk["activity_nr"] = chunk["activity_nr"].map(normalize_activity)
            target = chunk.loc[chunk["activity_nr"].isin(relevant_activity)].copy()
            if target.empty:
                continue
            if target["citation_id"].isna().any() or target["citation_id"].astype(str).str.strip().eq("").any():
                raise ValueError("相关Violation.citation_id存在空值；无法执行(activity_nr,citation_id)去重。")
            target["citation_id"] = target["citation_id"].astype(str).str.strip()
            target["_load_sort"] = pd.to_datetime(target["load_dt"], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").fillna("")
            target["_row_fingerprint"] = _row_sha256(target, violation_required)
            _append_sqlite(connection, "violation_stage", target[violation_required + ["_load_sort", "_row_fingerprint"]])
            stats["violation_relevant_rows_staged"] += len(target)
        if pd.isna(violation_max_load):
            raise ValueError("Violation.load_dt无法解析，不能确定共同快照日期。")
        violations, violation_duplicates = _deduplicated_frame(connection, "violation_stage", violation_required, ["activity_nr", "citation_id"])
    finally:
        connection.close()
    stats.update({"inspection_duplicates": inspection_duplicates, "violation_duplicates": violation_duplicates, "inspection_max_load_dt": inspection_max_load, "violation_max_load_dt": violation_max_load})
    return inspections, violations, stats


def main(root: Path, config_path: Path | None = None, allow_unverified_input: bool = False, fixture_as_of: str | None = None) -> None:
    cfg = load_config(config_path)
    root = root.resolve()
    ensure_dirs(root)
    fail_if_test_opened(root, "01_prepare_data")
    fail_if_analysis_frozen(root, "01_prepare_data")
    raw = root / "数据/00_原始数据"
    middle = root / "数据/01_中间数据"
    sealed = root / "数据/03_封存测试"
    audit = root / "结果/01_数据审计"
    if not allow_unverified_input:
        manifest = validate_download_manifest(root, cfg)
        as_of = pd.to_datetime(manifest["downloaded_at_utc"], utc=True).tz_localize(None)
        as_of_source = "数据/00_原始数据/download_manifest.json:downloaded_at_utc"
        secure_restricted_tree(root)
    else:
        if fixture_as_of is None:
            raise RuntimeError("self-test输入必须显式提供fixture_as_of；不得从测试表load_dt猜研究as-of。")
        as_of = pd.to_datetime(fixture_as_of, errors="raise", utc=True).tz_localize(None)
        as_of_source = "explicit self-test fixture_as_of (not a research snapshot)"

    # 正式原始表可能达到百万行。Inspection先分块全量暂存SQLite，按activity在SQL
    # 内确定最新行并筛目标NAICS；Violation仅暂存目标activity关联行。pandas只读取收敛子集。
    with tempfile.TemporaryDirectory(prefix=".osha_stage_", dir=middle) as temp_dir:
        inspections, violations, scan_stats = load_target_inputs(raw, cfg, Path(temp_dir) / "target_stage.sqlite")

    inspections["load_dt_parsed"] = pd.to_datetime(inspections["load_dt"], errors="coerce", utc=True).dt.tz_localize(None)
    inspections["open_date_parsed"] = pd.to_datetime(inspections["open_date"], errors="coerce")
    inspections["close_case_date_parsed"] = pd.to_datetime(inspections["close_case_date"], errors="coerce")
    violations["load_dt_parsed"] = pd.to_datetime(violations["load_dt"], errors="coerce", utc=True).dt.tz_localize(None)
    violations["issuance_date_parsed"] = pd.to_datetime(violations["issuance_date"], errors="coerce")
    inspection_max_load = scan_stats["inspection_max_load_dt"]
    violation_max_load = scan_stats["violation_max_load_dt"]
    # load_dt只作来源更新审计；正式研究as-of由完整下载manifest的下载时间冻结。
    snapshot_date = as_of

    inspection_duplicate_stats = scan_stats["inspection_duplicates"]
    violation_duplicate_stats = scan_stats["violation_duplicates"]
    duplicate_inspection_rows = int(inspection_duplicate_stats["rows_in_duplicate_groups"])
    duplicate_inspection_groups = int(inspection_duplicate_stats["duplicate_groups"])
    duplicate_violation_rows = int(violation_duplicate_stats["rows_in_duplicate_groups"])
    duplicate_violation_groups = int(violation_duplicate_stats["duplicate_groups"])

    inspections["naics_group"] = inspections["naics_code"].map(industry_group)
    proxy_values = inspections.apply(lambda row: entity_proxy(row, bool(cfg["rules"]["entity_address_fallback_to_site"])), axis=1, result_type="expand")
    proxy_values.columns = ["entity_proxy_id", "entity_address_source", "entity_street_normalized", "entity_zip5"]
    inspections = pd.concat([inspections, proxy_values], axis=1)

    restricted_columns = ["activity_nr", "entity_proxy_id", "estab_name", "mail_street", "mail_city", "mail_state", "mail_zip", "site_address", "site_city", "site_state", "site_zip", "entity_address_source"]
    inspections[restricted_columns].to_csv(middle / "受限_实体审计映射.csv", index=False, encoding="utf-8")
    audit_pairs = []
    pair_number = 0
    valid_entities = inspections[inspections["entity_proxy_id"].ne("")].copy()
    for entity_id, group in valid_entities.groupby("entity_proxy_id", sort=True):
        ordered = group.sort_values(["open_date_parsed", "activity_nr"])
        if len(ordered) > 1:
            left, right = ordered.iloc[0], ordered.iloc[-1]
            pair_number += 1
            audit_pairs.append({"audit_type": "key_internal_match", "pair_id": f"M{pair_number:04d}", "activity_nr_left": left["activity_nr"], "activity_nr_right": right["activity_nr"], "entity_proxy_id_left": entity_id, "entity_proxy_id_right": entity_id, "name_left": normalize_text(left["estab_name"]), "name_right": normalize_text(right["estab_name"]), "street_left": left["entity_street_normalized"], "street_right": right["entity_street_normalized"], "zip5": left["entity_zip5"], "name_similarity": difflib.SequenceMatcher(None, normalize_text(left["estab_name"]), normalize_text(right["estab_name"])).ratio()})
            if pair_number >= 200:
                break
    nonmatch_candidates = []
    for _, block in valid_entities.sort_values(["entity_zip5", "estab_name", "activity_nr"]).groupby("entity_zip5", sort=True):
        compact = block.drop_duplicates("entity_proxy_id").head(500)
        records = list(compact.itertuples(index=False))
        for left, right in zip(records, records[1:]):
            if left.entity_proxy_id == right.entity_proxy_id:
                continue
            similarity = difflib.SequenceMatcher(None, normalize_text(left.estab_name), normalize_text(right.estab_name)).ratio()
            nonmatch_candidates.append((similarity, left, right))
    for index, (similarity, left, right) in enumerate(sorted(nonmatch_candidates, key=lambda item: (-item[0], item[1].activity_nr, item[2].activity_nr))[:100], start=1):
        audit_pairs.append({"audit_type": "high_similarity_unmatched", "pair_id": f"U{index:04d}", "activity_nr_left": left.activity_nr, "activity_nr_right": right.activity_nr, "entity_proxy_id_left": left.entity_proxy_id, "entity_proxy_id_right": right.entity_proxy_id, "name_left": normalize_text(left.estab_name), "name_right": normalize_text(right.estab_name), "street_left": left.entity_street_normalized, "street_right": right.entity_street_normalized, "zip5": left.entity_zip5, "name_similarity": similarity})
    audit_pair_columns = ["audit_type", "pair_id", "activity_nr_left", "activity_nr_right", "entity_proxy_id_left", "entity_proxy_id_right", "name_left", "name_right", "street_left", "street_right", "zip5", "name_similarity"]
    audit_pair_frame = pd.DataFrame(audit_pairs, columns=audit_pair_columns)
    audit_pair_frame.to_csv(middle / "受限_实体复核候选.csv", index=False, encoding="utf-8")
    # 学生2/5使用的独立盲审视图不含连接键、实体ID、日期、结果或split；学生1
    # 仅在盲审完成后，使用上面的受限完整候选表合并判断并执行门禁。
    blind_frame = pd.DataFrame({
        "pair_id": audit_pair_frame.get("pair_id", pd.Series(dtype=str)),
        "audit_type": audit_pair_frame.get("audit_type", pd.Series(dtype=str)),
        "name_left_fragment": audit_pair_frame.get("name_left", pd.Series(dtype=str)).map(lambda value: blinded_fragment(value, 16)),
        "name_right_fragment": audit_pair_frame.get("name_right", pd.Series(dtype=str)).map(lambda value: blinded_fragment(value, 16)),
        "street_left_fragment": audit_pair_frame.get("street_left", pd.Series(dtype=str)).map(lambda value: blinded_fragment(value, 12)),
        "street_right_fragment": audit_pair_frame.get("street_right", pd.Series(dtype=str)).map(lambda value: blinded_fragment(value, 12)),
        "zip_fragment": audit_pair_frame.get("zip5", pd.Series(dtype=str)).fillna("").astype(str).map(lambda value: (value[:3] + "**") if value else ""),
        "name_similarity": pd.to_numeric(audit_pair_frame.get("name_similarity", pd.Series(dtype=float)), errors="coerce"),
    })
    blind_frame.to_csv(middle / "受限_实体盲审候选.csv", index=False, encoding="utf-8")
    write_json({"selection_rule": "key_internal: earliest/latest record per repeated proxy, deterministic first 200; unmatched: same ZIP5 block, adjacent normalized names, top 100 by difflib similarity", "key_internal_match_pairs": sum(row["audit_type"] == "key_internal_match" for row in audit_pairs), "high_similarity_unmatched_pairs": sum(row["audit_type"] == "high_similarity_unmatched" for row in audit_pairs), "manual_ppv_computed": False}, audit / "实体复核候选汇总.json")

    safe_columns = ["activity_nr", "entity_proxy_id", "entity_address_source", "reporting_id", "site_state", "naics_code", "naics_group", "insp_type", "insp_scope", "why_no_insp", "open_date_parsed", "close_case_date_parsed", "load_dt_parsed"]
    inspection_clean = inspections[safe_columns].rename(columns={"open_date_parsed": "open_date", "close_case_date_parsed": "close_case_date", "load_dt_parsed": "load_dt"})
    inspection_clean.to_csv(middle / "inspection_clean.csv", index=False, encoding="utf-8")

    deleted_values = {str(v).upper() for v in cfg["rules"]["deleted_violation_values"]}
    violations["is_deleted_current"] = violations["delete_flag"].fillna("").str.upper().isin(deleted_values).astype(int)
    violation_columns = ["activity_nr", "citation_id", "delete_flag", "is_deleted_current", "standard", "viol_type", "issuance_date_parsed", "rec", "gravity", "load_dt_parsed"]
    violation_clean = violations[violation_columns].rename(columns={"issuance_date_parsed": "issuance_date", "load_dt_parsed": "load_dt"})
    violation_clean.to_csv(middle / "violation_clean.csv", index=False, encoding="utf-8")
    # 学生2只接收分类所需的最小聚合字段。这里不输出activity_nr、citation_id、
    # 日期、单位或split，避免普通交接文件被用于重建Test结果。
    classification_input = (
        violations.assign(
            standard=violations["standard"].fillna("").astype(str).str.strip(),
            viol_type=violations["viol_type"].fillna("").astype(str).str.strip(),
        )
        .groupby(["standard", "viol_type"], dropna=False, as_index=False)
        .agg(citation_count=("citation_id", "size"), current_deleted_count=("is_deleted_current", "sum"))
        .sort_values(["standard", "viol_type"], kind="stable")
        .reset_index(drop=True)
    )
    classification_input.insert(0, "classification_record_id", [f"C{index:06d}" for index in range(1, len(classification_input) + 1)])
    classification_input.to_csv(audit / "风险分类输入_最小字段.csv", index=False, encoding="utf-8")
    violation_by_activity = {key: group for key, group in violations.groupby("activity_nr")}

    # 检查级历史结果账本：只有available_time早于c截止日的记录才可进入画像。
    historical_rows = []
    actual = set(cfg["rules"]["actual_inspection_scopes"])
    historical_base = inspections.loc[
        inspections["naics_group"].notna()
        & inspections["insp_scope"].isin(actual)
        & inspections["entity_proxy_id"].ne("")
        & inspections["open_date_parsed"].notna()
    ]
    for inspection_row in historical_base.itertuples(index=False):
        linked = violation_by_activity.get(inspection_row.activity_nr, violations.iloc[0:0])
        eligible = linked.loc[
            linked["issuance_date_parsed"].notna()
            & linked["issuance_date_parsed"].between(
                inspection_row.open_date_parsed,
                inspection_row.open_date_parsed + pd.Timedelta(days=int(cfg["rules"]["label_window_days"])),
            )
        ]
        outcome = int(not eligible.empty)
        if outcome:
            available_time = eligible["issuance_date_parsed"].min() + pd.Timedelta(days=int(cfg["rules"]["public_buffer_days"]))
        elif pd.notna(inspection_row.close_case_date_parsed):
            available_time = max(
                inspection_row.open_date_parsed + pd.Timedelta(days=int(cfg["rules"]["negative_maturity_days"])),
                inspection_row.close_case_date_parsed,
            )
        else:
            available_time = pd.NaT
        historical_rows.append({"activity_nr": inspection_row.activity_nr, "entity_proxy_id": inspection_row.entity_proxy_id, "open_date": inspection_row.open_date_parsed, "naics_group": inspection_row.naics_group, "site_state": inspection_row.site_state, "insp_type": inspection_row.insp_type, "outcome_positive": outcome, "outcome_available_time": available_time})
    pd.DataFrame(historical_rows).to_csv(middle / "historical_inspection_outcomes.csv", index=False, encoding="utf-8")

    planned = set(cfg["rules"]["primary_inspection_types"])
    candidates = inspections.loc[
        inspections["naics_group"].notna()
        & inspections["insp_scope"].isin(actual)
        & inspections["insp_type"].isin(planned)
        & inspections["entity_proxy_id"].ne("")
        & inspections["open_date_parsed"].notna()
    ].copy()
    if candidates.empty:
        raise ValueError("按互斥行业组、主候选insp_type和actual scope筛选后无样本。")
    candidates["quarter"] = candidates["open_date_parsed"].dt.to_period("Q").astype(str)
    episodes: list[dict[str, Any]] = []
    for (entity_id, quarter), group in candidates.groupby(["entity_proxy_id", "quarter"], sort=True):
        start = group["open_date_parsed"].min()
        end = group["open_date_parsed"].max()
        activity_ids = sorted(group["activity_nr"].tolist())
        eligible_parts = []
        for inspection_row in group.itertuples(index=False):
            if inspection_row.activity_nr not in violation_by_activity:
                continue
            linked_one = violation_by_activity[inspection_row.activity_nr]
            eligible_one = linked_one.loc[
                linked_one["issuance_date_parsed"].notna()
                & linked_one["issuance_date_parsed"].between(
                    inspection_row.open_date_parsed,
                    inspection_row.open_date_parsed + pd.Timedelta(days=int(cfg["rules"]["label_window_days"])),
                )
            ]
            if not eligible_one.empty:
                eligible_parts.append((inspection_row, eligible_one))
        eligible = pd.concat([part for _, part in eligible_parts], ignore_index=True) if eligible_parts else violations.iloc[0:0]
        label = int(not eligible.empty)
        if label:
            # OR标签一旦由任一组成Inspection的合格citation成立即不可逆；不等待同季度
            # 其他阴性/未关闭检查，按最早合格citation加公开缓冲确定可用时间。
            available = min(
                part["issuance_date_parsed"].min() for _, part in eligible_parts
            ) + pd.Timedelta(days=int(cfg["rules"]["public_buffer_days"]))
        else:
            component_available = []
            for inspection_row in group.itertuples(index=False):
                if bool(cfg["rules"]["require_closed_case_for_negative"]) and pd.isna(inspection_row.close_case_date_parsed):
                    component_available.append(pd.NaT)
                else:
                    component_available.append(max(
                        inspection_row.open_date_parsed + pd.Timedelta(days=int(cfg["rules"]["negative_maturity_days"])),
                        inspection_row.close_case_date_parsed,
                    ))
            # 只有episode仍为阴性时，才必须等全部组成Inspection成熟且（按主规则）关闭。
            available = pd.NaT if any(pd.isna(value) for value in component_available) else max(component_available)
        split = split_name(start, cfg["splits"])
        sample_material = f"{entity_id}|{quarter}|{'|'.join(activity_ids)}"
        context_row = group.sort_values(["open_date_parsed", "activity_nr"], kind="stable").iloc[0]
        episodes.append({
            "sample_id": hashlib.sha256(sample_material.encode("utf-8")).hexdigest()[:20],
            "entity_proxy_id": entity_id,
            "quarter": quarter,
            "episode_open_date": start,
            "episode_last_open_date": end,
            "activity_nrs": "|".join(activity_ids),
            "activity_count": len(activity_ids),
            "context_activity_nr": context_row["activity_nr"],
            "candidate_naics_group": context_row["naics_group"],
            "candidate_site_state": context_row["site_state"] if pd.notna(context_row["site_state"]) else "UNKNOWN",
            "label": label,
            "label_available_date": available,
            "has_unclosed_component": bool(group["close_case_date_parsed"].isna().any()),
            "split": split,
        })
    episode_frame = pd.DataFrame(episodes)
    freeze = pd.Timestamp(cfg["splits"]["model_freeze_date"])
    episode_frame["maturity_boundary"] = snapshot_date
    train_val = episode_frame["split"].isin(["train", "validation"])
    episode_frame.loc[train_val, "maturity_boundary"] = freeze
    episode_frame["is_mature_at_as_of"] = episode_frame["label_available_date"].notna() & (episode_frame["label_available_date"] <= snapshot_date)
    episode_frame.loc[train_val, "is_mature_for_use"] = episode_frame.loc[train_val, "label_available_date"].notna() & (episode_frame.loc[train_val, "label_available_date"] < freeze)
    episode_frame.loc[episode_frame["split"].eq("test"), "is_mature_for_use"] = episode_frame.loc[episode_frame["split"].eq("test"), "is_mature_at_as_of"]
    episode_frame["is_mature_for_use"] = episode_frame["is_mature_for_use"].eq(True)
    usable = episode_frame.loc[episode_frame["is_mature_for_use"] & episode_frame["split"].isin(["train", "validation", "test"])].copy()
    if usable.loc[usable["split"].isin(["train", "validation"])].empty:
        raise ValueError("没有在模型冻结日前成熟的Train/Validation样本。")

    public_episode = usable.copy()
    public_episode.loc[public_episode["split"].eq("test"), ["label", "label_available_date"]] = pd.NA
    public_episode.to_csv(middle / "inspection_episode.csv", index=False, encoding="utf-8")
    test_labels = usable.loc[usable["split"].eq("test"), ["sample_id", "entity_proxy_id", "quarter", "label", "label_available_date"]].copy()
    test_label_path = sealed / "sealed_test_labels.csv"
    test_labels.to_csv(test_label_path, index=False, encoding="utf-8")
    write_json({
        "path": str(test_label_path.relative_to(root)),
        "sha256": sha256_file(test_label_path),
        "rows": len(test_labels),
        "test_candidate_total": int(episode_frame["split"].eq("test").sum()),
        "test_mature_rows": int((episode_frame["split"].eq("test") & episode_frame["is_mature_for_use"]).sum()),
        "columns": list(test_labels.columns),
        "positive_count_disclosed": False,
        "created_by_stage": "01_prepare_data.py",
    }, sealed / "sealed_test_commitment.json")

    flow = pd.DataFrame([
        {"step": "Inspection原始行", "n": int(scan_stats["inspection_raw_rows_scanned"])},
        {"step": "全量Inspection磁盘暂存行", "n": int(scan_stats["inspection_rows_staged"])},
        {"step": "最新行仍属于目标行业", "n": int(scan_stats["inspection_target_latest_rows"])},
        {"step": "目标行业activity_nr去重后", "n": len(inspections)},
        {"step": "互斥行业组", "n": int(inspections["naics_group"].notna().sum())},
        {"step": "实际检查scope", "n": int((inspections["naics_group"].notna() & inspections["insp_scope"].isin(actual)).sum())},
        {"step": "主候选Planned检查", "n": len(candidates)},
        {"step": "entity×quarter episode", "n": len(episode_frame)},
        {"step": "成熟Train/Validation/Test", "n": len(usable)},
    ])
    flow.to_csv(audit / "样本排除流.csv", index=False, encoding="utf-8")
    fig_height = max(5.0, 0.72 * len(flow))
    fig, ax = plt.subplots(figsize=(9.5, fig_height))
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.5, len(flow) - 0.5)
    ax.axis("off")
    for index, row in flow.reset_index(drop=True).iterrows():
        y = len(flow) - 1 - index
        ax.text(0.5, y, f"{row['step']}\nN = {int(row['n']):,}", ha="center", va="center",
                bbox={"boxstyle": "round,pad=0.35", "facecolor": "#EAF2F8", "edgecolor": "#2E6F9E"})
        if index < len(flow) - 1:
            ax.annotate("", xy=(0.5, y - 0.62), xytext=(0.5, y - 0.38),
                        arrowprops={"arrowstyle": "->", "color": "#536878", "lw": 1.2})
    plt.tight_layout()
    plt.savefig(audit / "图2_样本筛选流程.svg", format="svg")
    plt.close(fig)
    maturity_base = episode_frame.loc[episode_frame["split"].isin(["train", "validation", "embargo", "test"])].copy()
    maturity_base["mature_positive_flag"] = maturity_base["is_mature_for_use"] & maturity_base["label"].eq(1)
    maturity_base["mature_negative_flag"] = maturity_base["is_mature_for_use"] & maturity_base["label"].eq(0)
    maturity_base["immature_open_case_flag"] = ~maturity_base["is_mature_for_use"] & maturity_base["label"].eq(0) & maturity_base["has_unclosed_component"]
    maturity_base["immature_other_flag"] = ~maturity_base["is_mature_for_use"] & ~maturity_base["immature_open_case_flag"]
    maturity_audit = (
        maturity_base.groupby(["split", "quarter"], as_index=False)
        .agg(
            candidate_total=("sample_id", "size"),
            mature_positive=("mature_positive_flag", "sum"),
            mature_negative=("mature_negative_flag", "sum"),
            immature_open_case=("immature_open_case_flag", "sum"),
            immature_other=("immature_other_flag", "sum"),
        )
    )
    maturity_audit["mature_total"] = maturity_audit["mature_positive"] + maturity_audit["mature_negative"]
    maturity_audit["mature_rate"] = maturity_audit["mature_total"] / maturity_audit["candidate_total"]
    maturity_audit["maturity_boundary"] = maturity_audit["split"].map(lambda value: freeze if value in {"train", "validation"} else snapshot_date)
    # 开封前不披露Test成熟样本的阳性/阴性构成；总候选、成熟总数和成熟率仍可审计差异性删失。
    maturity_audit.loc[maturity_audit["split"].eq("test"), ["mature_positive", "mature_negative"]] = pd.NA
    maturity_audit.to_csv(audit / "成熟度审计.csv", index=False, encoding="utf-8")
    used_audit = usable.groupby(["split", "quarter"], as_index=False).agg(n=("sample_id", "size"), positives=("label", "sum"))
    split_audit = maturity_audit.merge(used_audit, on=["split", "quarter"], how="left")
    split_audit["n"] = split_audit["n"].fillna(0).astype(int)
    split_audit.loc[split_audit["split"].eq("test"), "positives"] = pd.NA
    split_audit.to_csv(audit / "时间切分审计.csv", index=False, encoding="utf-8")
    join_match = int(violations["activity_nr"].isin(inspections["activity_nr"]).sum())
    write_json({
        "inspection_rows_in_duplicate_groups": duplicate_inspection_rows,
        "inspection_duplicate_groups": duplicate_inspection_groups,
        "inspection_rows_removed": duplicate_inspection_rows - duplicate_inspection_groups,
        "violation_rows_in_duplicate_groups": duplicate_violation_rows,
        "violation_duplicate_groups": duplicate_violation_groups,
        "violation_rows_removed": duplicate_violation_rows - duplicate_violation_groups,
        "inspection_unique_activity_nr": len(inspections),
        "violation_unique_activity_citation": len(violations),
        "violation_rows_matched_to_inspection": join_match,
        "violation_rows_unmatched": len(violations) - join_match,
        "dedup_rule": "latest load_dt; lexicographically largest SHA-256 row fingerprint on exact load_dt tie",
        "audit_scope": "Inspection duplicate groups are counted over all staged Inspection rows, while the returned research subset is latest-row target-NAICS filtered inside SQL; Violation duplicate groups are counted among rows linked to that target Inspection set.",
    }, audit / "连接与去重审计.json")
    write_json({
        "csv_chunk_rows": int(cfg.get("performance", {}).get("csv_chunk_rows", 100_000)),
        "inspection_raw_rows_scanned": int(scan_stats["inspection_raw_rows_scanned"]),
        "inspection_rows_staged": int(scan_stats["inspection_rows_staged"]),
        "inspection_target_latest_rows": int(scan_stats["inspection_target_latest_rows"]),
        "violation_raw_rows_scanned": int(scan_stats["violation_raw_rows_scanned"]),
        "violation_relevant_rows_staged": int(scan_stats["violation_relevant_rows_staged"]),
        "strategy": "chunked full Inspection -> SQLite deterministic latest-row dedup and SQL target-NAICS filter -> related-activity Violation staging/dedup -> pandas research subset",
        "full_raw_tables_loaded_into_memory": False,
    }, audit / "规模处理审计.json")
    write_json({
        "as_of": snapshot_date,
        "as_of_source": as_of_source,
        "inspection_max_load_dt": inspection_max_load,
        "violation_max_load_dt": violation_max_load,
        "inspection_sha256": sha256_file(raw / "osha_inspection.csv"),
        "violation_sha256": sha256_file(raw / "osha_violation.csv"),
        "industry_groups": cfg["rules"]["industry_groups"],
        "primary_inspection_types": cfg["rules"]["primary_inspection_types"],
        "actual_inspection_scopes": cfg["rules"]["actual_inspection_scopes"],
        "delete_policy_primary": cfg["rules"]["primary_delete_policy"],
        "episode_label_rule": "each component Inspection uses its own inclusive [open_date, open_date+180d] window; entity-quarter label is OR",
        "episode_positive_available_rule": "earliest eligible citation issuance_date + public_buffer_days; no wait for other components",
        "episode_negative_available_rule": "only for OR-negative episode: max(each open_date+negative_maturity_days, each close_case_date); any required unclosed component => unavailable",
        "reserved_not_executed": ["insp_type I/K", "exclude-deleted alternative label", "tree model", "alternative half-life"],
        "label_window_days": cfg["rules"]["label_window_days"],
        "negative_maturity_days": cfg["rules"]["negative_maturity_days"],
        "splits": cfg["splits"],
    }, audit / "字段与规则快照.json")
    included_after_as_of = usable["label_available_date"].notna() & (usable["label_available_date"] > snapshot_date)
    if included_after_as_of.any():
        raise RuntimeError("存在label_available_date晚于冻结as-of却被纳入的episode。")
    if not allow_unverified_input:
        permission_audit = secure_restricted_tree(root)
        write_json(permission_audit, audit / "受限目录权限审计.json")
        if permission_audit.get("supported") is not True:
            raise RuntimeError("正式运行平台无法执行POSIX 0700/0600权限门；请改用等价ACL或独立账户环境。")
    print(f"01完成: {len(usable)}个成熟episode；Test标签已单独封存。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--self-test-input", action="store_true", help="仅供包内离线smoke fixture；正式分析禁用。")
    parser.add_argument("--fixture-as-of", default=None, help="仅与--self-test-input同时使用的显式样例as-of；不属于研究快照。")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        main(args.root, args.config, args.self_test_input, args.fixture_as_of)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
