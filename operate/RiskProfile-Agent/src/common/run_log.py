"""统一运行日志与实验输出保护。

约定：
- 每次实验写入独立的 runs/<时间戳>_<名称>/ 目录，目录已存在时拒绝覆盖；
- 日志为 JSONL，固定记录字段见 configs/config.json#logging；
- 实验输出附带 SHA-256 清单（output_manifest.json），支持阶段11的承诺流程。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RunLog:
    """JSONL 运行日志：每个事件一行，字段统一为 ts_utc/event + 附加字段。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def log(self, event: str, **fields: Any) -> None:
        record = {"ts_utc": utc_now_iso(), "event": event}
        record.update(fields)
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def new_run_dir(
    base: Path,
    run_name: str,
    config: dict | None = None,
    _timestamp: str | None = None,
    _unique: str | None = None,
) -> Path:
    """创建不可覆盖的实验运行目录；目录已存在时抛错，绝不静默覆盖。"""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = _unique or uuid.uuid4().hex[:6]
    path = base / f"{timestamp}_{run_name}_{unique}"
    if path.exists():
        raise FileExistsError(f"运行目录已存在，拒绝覆盖：{path}")
    path.mkdir(parents=False)
    if config is not None:
        (path / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return path


def write_output_manifest(run_dir: Path, files: dict[str, Path]) -> Path:
    """记录运行目录内输出文件的 SHA-256，返回 manifest 路径。"""
    manifest: dict[str, dict[str, str]] = {}
    for name, path in files.items():
        manifest[name] = {
            "file": str(path.name),
            "sha256": sha256_file(path),
        }
    out = Path(run_dir) / "output_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
