from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from pipeline_common import DEFAULT_ROOT, ensure_dirs, fail_if_analysis_frozen, fail_if_test_opened, load_config, secure_restricted_tree, sha256_file, sha256_text, write_json


def build_params(endpoint: dict[str, Any], limit: int, offset: int, filter_enabled: bool) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset, "sort": "asc", "sort_by": endpoint["sort_by"]}
    if filter_enabled:
        params["filter_object"] = json.dumps(endpoint["filter_object"], ensure_ascii=False, separators=(",", ":"))
    return params


def extract_column_names(payload: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"column_name", "field_name", "name"} and isinstance(value, str):
                if any(marker in payload for marker in ("data_type", "intended_datatype", "column_desc", "description")):
                    names.add(value.strip().lower())
            names.update(extract_column_names(value))
    elif isinstance(payload, list):
        for item in payload:
            names.update(extract_column_names(item))
    return names


def request_json(session: requests.Session, url: str, *, params: dict[str, Any] | None, headers: dict[str, str] | None, timeout: int, attempts: int = 4) -> tuple[Any, dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None, dict(response.headers)
            return response.json(), dict(response.headers)
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"DOL API请求失败（密钥和请求头未写入日志）: {type(last_error).__name__}") from last_error


def fetch_catalog_metadata(session: requests.Session, root: Path, cfg: dict[str, Any]) -> dict[str, set[str]]:
    raw_dir = root / "数据/00_原始数据"
    reference = root / "官方资料/DOL_OSHA字段元数据_20260812.csv"
    if not reference.exists():
        raise FileNotFoundError(f"缺少包内字段基线: {reference}")
    baseline = pd.read_csv(reference, dtype=str)
    found: dict[str, set[str]] = {}
    diffs: dict[str, Any] = {}
    for name, endpoint in cfg["download"]["endpoints"].items():
        url = f"{cfg['download']['catalog_base_url'].rstrip('/')}/datasets/{endpoint['dataset_id']}"
        payload, _ = request_json(session, url, params=None, headers=None, timeout=cfg["download"]["timeout_seconds"])
        write_json(payload, raw_dir / f"metadata_{name}.json")
        columns = extract_column_names(payload)
        expected_all = set(baseline.loc[baseline["dataset"].str.lower() == name, "column_name"].str.lower())
        required = set(cfg["schema"][f"{name}_required"])
        found[name] = columns
        diffs[name] = {"missing_required": sorted(required - columns), "added_vs_packaged_baseline": sorted(columns - expected_all), "missing_vs_packaged_baseline": sorted(expected_all - columns)}
    write_json(diffs, raw_dir / "schema_diff.json")
    failures = {name: detail["missing_required"] for name, detail in diffs.items() if detail["missing_required"]}
    if failures:
        raise RuntimeError(f"官方schema与程序关键字段不一致，已写入schema_diff.json并停止: {failures}")
    return found


def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def reject_completed_snapshot(root: Path) -> None:
    """A completed download is immutable; a new as-of requires a fresh snapshot area."""
    manifest_path = root / "数据/00_原始数据/download_manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("complete") is True:
        raise RuntimeError(
            "当前数据目录已有complete=true的正式下载快照；禁止复用旧分片更新downloaded_at/as-of。"
            "如需新快照，请先将整个数据/00_原始数据归档到新版本目录，再在空目录重新下载。"
        )


def download_endpoint(session: requests.Session, root: Path, cfg: dict[str, Any], name: str, api_key: str, max_pages: int | None) -> dict[str, Any]:
    raw_dir = root / "数据/00_原始数据"
    parts_dir = raw_dir / ".parts" / name
    parts_dir.mkdir(parents=True, exist_ok=True)
    endpoint = cfg["download"]["endpoints"][name]
    limit = int(cfg["download"]["page_limit"])
    url = f"{cfg['download']['base_url'].rstrip('/')}/get/OSHA/{endpoint['api_name']}/json"
    filter_enabled = bool(cfg["download"].get("filter_enabled", True))
    fingerprint_payload = {"url": url, "limit": limit, "sort": "asc", "sort_by": endpoint["sort_by"], "filter_enabled": filter_enabled, "filter_object": endpoint.get("filter_object")}
    fingerprint = sha256_text(json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False))
    state_path = parts_dir / "resume_state.json"
    state = {"fingerprint": fingerprint, "next_offset": 0, "pages": 0, "rows": 0}
    if state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        if prior.get("fingerprint") != fingerprint:
            raise RuntimeError(f"{name}下载参数已改变；为避免混合分片，请人工移走 {parts_dir} 后重试。")
        if prior.get("complete") is True:
            output = raw_dir / endpoint["output"]
            print(f"{name}已完成（断点续传跳过）：{output}")
            return {
                "dataset": name,
                "complete": True,
                "rows": int(prior.get("rows", 0)),
                "pages": int(prior.get("pages", 0)),
                "output": str(output.relative_to(root)) if output.exists() else None,
                "sha256": sha256_file(output) if output.exists() else None,
                "request": fingerprint_payload,
                "resume_state": str(state_path.relative_to(root)),
            }
        state = prior
    page_calls = 0
    completed = False
    while max_pages is None or page_calls < max_pages:
        offset = int(state["next_offset"])
        part_path = parts_dir / f"part_{offset:012d}.csv"
        if part_path.exists():
            rows = count_csv_rows(part_path)
        else:
            params = build_params(endpoint, limit, offset, filter_enabled)
            params["X-API-KEY"] = api_key
            payload, _ = request_json(session, url, params=params, headers=None, timeout=int(cfg["download"]["timeout_seconds"]))
            if payload is None:
                rows = 0
            else:
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise RuntimeError(f"{name}响应中没有data数组，已停止；请在DOL API Query Builder核对端点和过滤条件。")
                records = payload["data"]
                rows = len(records)
            if rows:
                pd.DataFrame.from_records(records).to_csv(part_path, index=False, encoding="utf-8")
                part_path.chmod(0o600)
        page_calls += 1
        if rows == 0:
            completed = True
            break
        # 5 MB上限可使rows < limit，因此只按实际行数推进，短页不是结束信号。
        state = {"fingerprint": fingerprint, "next_offset": offset + rows, "pages": int(state["pages"]) + 1, "rows": int(state["rows"]) + rows}
        write_json(state, state_path)
        state_path.chmod(0o600)
    output = raw_dir / endpoint["output"]
    if completed:
        parts = sorted(parts_dir.glob("part_*.csv"))
        if not parts:
            raise RuntimeError(f"{name}过滤后返回0行；未生成正式数据文件。请在Query Builder核对日期格式。")
        sqlite_path = parts_dir / "merge_sort.sqlite"
        connection = sqlite3.connect(sqlite_path)
        try:
            first = True
            for part in parts:
                chunk = pd.read_csv(part, dtype=str, low_memory=False)
                chunk.to_sql("records", connection, if_exists="replace" if first else "append", index=False)
                first = False
            columns = [row[1] for row in connection.execute("PRAGMA table_info(records)")]
            sort_cols = ["activity_nr"] + (["citation_id"] if name == "violation" and "citation_id" in columns else [])
            query = f'SELECT * FROM records ORDER BY {", ".join(chr(34) + col + chr(34) for col in sort_cols)}'
            first_chunk = True
            for chunk in pd.read_sql_query(query, connection, chunksize=50000):
                chunk.to_csv(output, mode="w" if first_chunk else "a", header=first_chunk, index=False, encoding="utf-8")
                first_chunk = False
            output.chmod(0o600)
        finally:
            connection.close()
        sqlite_path.unlink(missing_ok=True)
        # 只有合并、排序和最终文件hash均成功后才把本端点标记为完成；
        # 这样合并失败仍可安全续跑，不会被complete门永久锁死。
        output_hash = sha256_file(output)
        state = {**state, "complete": True, "completed_at_utc": datetime.now(timezone.utc).isoformat(), "output_sha256": output_hash}
        write_json(state, state_path)
        state_path.chmod(0o600)
    else:
        output_hash = None
    return {
        "dataset": name,
        "complete": completed,
        "rows": int(state["rows"]),
        "pages": int(state["pages"]),
        "output": str(output.relative_to(root)) if completed else None,
        "sha256": output_hash,
        "request": fingerprint_payload,
        "resume_state": str(state_path.relative_to(root)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从官方DOL API下载OSHA Inspection和Violation快照。")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--metadata-only", action="store_true", help="只取无需密钥的数据集目录元数据并比对schema。")
    parser.add_argument("--max-pages", type=int, default=None, help="仅供端点核验；限制页数时不声称完整。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    root = args.root.resolve()
    ensure_dirs(root)
    fail_if_test_opened(root, "00_download_official_data")
    fail_if_analysis_frozen(root, "00_download_official_data")
    secure_restricted_tree(root)
    if not args.metadata_only:
        reject_completed_snapshot(root)
    session = requests.Session()
    fetch_catalog_metadata(session, root, cfg)
    if args.metadata_only:
        secure_restricted_tree(root)
        print("元数据与关键字段基线比对完成；未请求或记录API密钥。")
        return 0
    api_key = os.environ.get("DOL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未设置环境变量DOL_API_KEY。程序不从文件读取密钥，也不会把密钥写入URL或日志。")
    entries = [download_endpoint(session, root, cfg, name, api_key, args.max_pages) for name in ("inspection", "violation")]
    manifest = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "complete": all(item["complete"] for item in entries),
        "api_key_recorded": False,
        "datasets": entries,
    }
    write_json(manifest, root / "数据/00_原始数据/download_manifest.json")
    secure_restricted_tree(root)
    if not manifest["complete"]:
        print("已按--max-pages完成端点核验；下载未到0行终止页，不视为全量，未生成正式合并文件。")
    else:
        print("官方数据下载完成；行数、请求参数与SHA-256已写入download_manifest.json。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
