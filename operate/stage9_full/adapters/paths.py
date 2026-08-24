"""路径与配置解析：定位 stage9_full / 仓库根 / 阶段八代码，构建运行期配置。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

STAGE9_FULL = Path(__file__).resolve().parents[1]
REPO_ROOT = STAGE9_FULL.parents[1]
STAGE1 = REPO_ROOT / "operate" / "stage1"


def setup_paths() -> tuple[Path, Path]:
    for p in (str(STAGE9_FULL), str(STAGE1)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return STAGE9_FULL, STAGE1


setup_paths()


def load_experiment_config() -> dict[str, Any]:
    return json.loads((STAGE9_FULL / "config" / "experiment_config.json").read_text(encoding="utf-8"))


def data_root() -> Path:
    env = os.getenv("RP_DATA_ROOT")
    return Path(env) if env else REPO_ROOT


def resolve_data_paths(config: dict[str, Any]) -> dict[str, Path]:
    root = data_root()
    return {key: (root / rel).resolve() for key, rel in (config.get("data") or {}).items()}


def build_stage_config(config: dict[str, Any], data: dict[str, Path]) -> dict[str, Any]:
    def abs_repo(rel: str) -> str:
        return str((REPO_ROOT / rel).resolve())

    merged: dict[str, Any] = dict(config)
    merged["data_resolved"] = {k: str(v) for k, v in data.items()}
    merged["paths"] = {
        "knowledge_chunks": str(data["knowledge_dir"] / "chunks" / "regulation_chunks.jsonl"),
        "standard_mapping": str(data["knowledge_dir"] / "standard_document_mapping.csv"),
        "whitelist": str(data["whitelist"]),
        "runs": str(STAGE9_FULL / "results"),
        "manifests": str(STAGE9_FULL / "results"),
    }
    merged["prompts"] = {
        "review_agent": abs_repo(config["prompts"]["review_agent"]),
        "semantic_audit": abs_repo(config["prompts"]["semantic_audit"]),
    }
    merged["registries"] = {
        "agent_registry": abs_repo(config["registries"]["agent_registry"]),
        "prompt_registry": abs_repo(config["registries"]["prompt_registry"]),
        "forbidden_claim_rules": abs_repo(config["registries"]["forbidden_claim_rules"]),
    }
    merged["audit"]["forbidden_rules_path"] = abs_repo(config["audit"]["forbidden_rules_path"])
    return merged
