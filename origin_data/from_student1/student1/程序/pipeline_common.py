from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml
from sklearn.metrics import average_precision_score


PROGRAM_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = PROGRAM_DIR.parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or PROGRAM_DIR / "config.yaml"
    with target.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_dirs(root: Path) -> None:
    for rel in [
        "数据/00_原始数据", "数据/01_中间数据", "数据/02_分析数据", "数据/03_封存测试",
        "结果/01_数据审计", "结果/02_画像", "结果/03_验证", "结果/04_正式测试_封存", "结果/05_论文图表",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)


def fail_if_test_opened(root: Path, stage: str) -> None:
    """Prevent any upstream write after the first formal Test-opening attempt."""
    test_dir = root / "结果/04_正式测试_封存"
    evidence = [test_dir / "test_open_attempt.json", test_dir / "test_open_record.json"]
    found = [str(path.relative_to(root)) for path in evidence if path.exists()]
    if found:
        raise RuntimeError(f"{stage}检测到Test已尝试/完成开封，禁止覆盖任何上游产物: {found}")


def fail_if_analysis_frozen(root: Path, stage: str) -> None:
    """Stop mutable upstream stages once the model freeze or Test prediction exists."""
    evidence = [
        root / "结果/03_验证/model_freeze_record.json",
        root / "结果/03_验证/test_predictions_sealed.csv",
        root / "结果/03_验证/test_prediction_commitment.json",
    ]
    found = [str(path.relative_to(root)) for path in evidence if path.exists()]
    if found:
        raise RuntimeError(f"{stage}检测到模型已冻结或Test预测已生成，禁止覆盖上游数据/画像: {found}")


def _icacls_restrict(path: Path, username: str, is_dir: bool) -> bool:
    """On Windows, remove inherited permissions and grant full control only to the current user."""
    perms = "(OI)(CI)F" if is_dir else "F"
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:{perms}"],
            check=True, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def secure_restricted_tree(root: Path, include_test_results: bool = False) -> dict[str, Any]:
    """Apply owner-only modes and return an auditable mode inventory.

    On POSIX systems, uses chmod 0700/0600. On Windows, uses icacls to
    remove inherited permissions and grant full control only to the
    current user (equivalent to POSIX owner-only). On other systems,
    returns ``supported=False``.
    """
    relative_dirs = ["数据/00_原始数据", "数据/01_中间数据", "数据/03_封存测试"]
    if include_test_results:
        relative_dirs.append("结果/04_正式测试_封存")
    inventory: list[dict[str, Any]] = []
    if os.name == "nt":
        username = os.environ.get("USERNAME", "")
        for relative in relative_dirs:
            directory = root / relative
            directory.mkdir(parents=True, exist_ok=True)
            _icacls_restrict(directory, username, is_dir=True)
            for path in sorted(directory.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError(f"受限目录中不允许符号链接: {path}")
                if path.is_dir():
                    _icacls_restrict(path, username, is_dir=True)
                elif path.is_file():
                    _icacls_restrict(path, username, is_dir=False)
            inventory.append({"path": relative, "kind": "directory", "mode_octal": "N/A", "passed": True})
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    inventory.append({"path": str(path.relative_to(root)), "kind": "file", "mode_octal": "N/A", "passed": True})
        return {"supported": True, "control": "Windows ACL owner-only; icacls inheritance removed, current user granted full control only", "paths": inventory}
    if os.name != "posix":
        return {"supported": False, "reason": "non-posix; formal run requires equivalent ACL/separate-account control", "paths": inventory}
    for relative in relative_dirs:
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"受限目录中不允许符号链接: {path}")
            if path.is_dir():
                path.chmod(0o700)
            elif path.is_file():
                path.chmod(0o600)
        mode = stat.S_IMODE(directory.stat().st_mode)
        inventory.append({"path": relative, "kind": "directory", "mode_octal": format(mode, "04o"), "passed": mode == 0o700})
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                mode = stat.S_IMODE(path.stat().st_mode)
                inventory.append({"path": str(path.relative_to(root)), "kind": "file", "mode_octal": format(mode, "04o"), "passed": mode == 0o600})
    failed = [item for item in inventory if not item["passed"]]
    if failed:
        raise RuntimeError(f"受限目录权限审计失败: {failed[:10]}")
    return {"supported": True, "control": "POSIX owner-only modes; same Unix account still requires separate environments/accounts or ACL", "paths": inventory}


def require_columns(frame: pd.DataFrame, required: Iterable[str], source: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{source}缺少必需字段: {missing}。程序已停止，请核对官方metadata和config.yaml，不要猜测字段。")


def read_csv_strict(path: Path, required: Iterable[str], source: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"未找到{source}: {path}")
    frame = pd.read_csv(path, dtype=str, low_memory=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    require_columns(frame, required, source)
    return frame


def normalize_activity(value: Any) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    return re.sub(r"\.0+$", "", text)


def normalize_text(value: Any) -> str:
    text = "" if pd.isna(value) else str(value).upper().strip()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def industry_group(value: Any) -> str | None:
    code = re.sub(r"\D", "", "" if pd.isna(value) else str(value))[:6]
    if code == "221122":
        return "221122"
    if code.startswith("2211"):
        return "2211_other"
    if code == "237130":
        return "237130"
    if code == "238210":
        return "238210"
    return None


def quarter_string(date: pd.Timestamp) -> str:
    return str(date.to_period("Q"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def rank_metrics(group: pd.DataFrame, score_col: str, fraction: float, weight_col: str | None = None) -> dict[str, float]:
    work = group.sort_values([score_col, "sample_id"], ascending=[False, True]).copy()
    weights = work[weight_col].to_numpy(float) if weight_col else np.ones(len(work))
    y = work["label"].to_numpy(int)
    scores = work[score_col].to_numpy(float)
    total_weight = float(weights.sum())
    positives = float(np.dot(weights, y))
    k = max(1, math.ceil(fraction * total_weight))
    cumulative_before = np.r_[0.0, np.cumsum(weights)[:-1]]
    selected_weights = np.clip(k - cumulative_before, 0.0, weights)
    hit = float(np.dot(selected_weights, y))
    selected_total = float(selected_weights.sum())
    precision = hit / max(selected_total, 1.0)
    recall = hit / positives if positives else float("nan")
    if (weights < 0).any():
        raise ValueError("排序指标权重不得为负。")
    # AP采用标准的分数阈值定义；相同score作为一个阈值组整体处理，sample_id
    # 只用于Recall@20%的容量边界确定性顺序，绝不改变AP。
    if positives:
        ap = float(average_precision_score(y, scores, sample_weight=weights))
    else:
        ap = float("nan")
    brier = float(np.average((scores - y) ** 2, weights=weights))
    return {"n": total_weight, "positives": positives, "selected_n": selected_total, "hits_at_20": hit, "ap": ap, "recall_at_20": recall, "precision_at_20": precision, "brier": brier}


def quarterly_metrics(predictions: pd.DataFrame, method_score_columns: dict[str, str], fraction: float, weight_col: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for quarter, group in predictions.groupby("quarter", sort=True):
        for method, column in method_score_columns.items():
            rows.append({"quarter": quarter, "method": method, **rank_metrics(group, column, fraction, weight_col)})
    return pd.DataFrame(rows)


def summarize_quarters(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, group in metrics.groupby("method"):
        weights = group["n"].to_numpy(float)
        total_positives = float(group["positives"].sum())
        total_hits = float(group["hits_at_20"].sum())
        total_selected = float(group["selected_n"].sum())
        row: dict[str, Any] = {"method": method, "quarters": len(group), "n": weights.sum(), "positives": total_positives, "selected_n": total_selected, "hits_at_20": total_hits}
        for metric in ["ap", "brier"]:
            valid = group[metric].notna()
            row[metric] = float(np.average(group.loc[valid, metric], weights=weights[valid])) if valid.any() else float("nan")
        row["recall_at_20"] = total_hits / total_positives if total_positives else float("nan")
        row["precision_at_20"] = total_hits / total_selected if total_selected else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def calibration_bins(predictions: pd.DataFrame, methods: dict[str, str], bins: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    edges = np.linspace(0, 1, bins + 1)
    for method, col in methods.items():
        work = predictions[[col, "label"]].dropna().copy()
        work["bin"] = pd.cut(work[col], edges, include_lowest=True, duplicates="drop")
        for interval, group in work.groupby("bin", observed=True):
            rows.append({"method": method, "bin": str(interval), "n": len(group), "mean_score": group[col].mean(), "observed_rate": group["label"].mean()})
    return pd.DataFrame(rows)
