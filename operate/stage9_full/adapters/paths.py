"""Stage9 自包含路径与配置解析。

项目根 = stage9_full 本身。运行时**不依赖**其他 stage、外部交付数据目录或仓库根；
所有路径默认在 stage9_full 内部解析（代码、配置、数据全部自包含）。

数据根解析顺序：
1. 环境变量 ``RP_DATA_ROOT``（若设置，须指向包含相同 data/ 布局的目录）；
2. 否则默认 = stage9_full 本身（data/ 就在 stage9_full/data/ 下）。

无论 Python 当前工作目录在哪，都通过 ``Path(__file__)`` 锚定项目根。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

STAGE9_FULL = Path(__file__).resolve().parents[1]


def setup_paths() -> Path:
    """把 stage9_full 加入 sys.path（幂等），返回项目根。"""
    root = str(STAGE9_FULL)
    if root not in sys.path:
        sys.path.insert(0, root)
    return STAGE9_FULL


setup_paths()


def stage9_root() -> Path:
    return STAGE9_FULL


def load_experiment_config() -> dict[str, Any]:
    return json.loads((STAGE9_FULL / "config" / "experiment_config.json").read_text(encoding="utf-8"))


def data_root() -> Path:
    env = os.getenv("RP_DATA_ROOT")
    return Path(env).resolve() if env else STAGE9_FULL


def resolve_data_paths(config: dict[str, Any]) -> dict[str, Path]:
    root = data_root()
    return {key: (root / rel).resolve() for key, rel in (config.get("data") or {}).items()}


def _abs(rel: str) -> str:
    return str((STAGE9_FULL / rel).resolve())


def build_stage_config(config: dict[str, Any], data: dict[str, Path]) -> dict[str, Any]:
    """把相对路径统一解析为 stage9_full 内部绝对路径，构建运行期配置。"""
    merged: dict[str, Any] = json.loads(json.dumps(config))
    merged["data_resolved"] = {k: str(v) for k, v in data.items()}
    merged["paths"] = {
        "knowledge_chunks": str(data["knowledge_dir"] / "chunks" / "regulation_chunks.jsonl"),
        "standard_mapping": str(data["knowledge_dir"] / "standard_document_mapping.csv"),
        "knowledge_dir": str(data["knowledge_dir"]),
        "whitelist": str(data["whitelist"]),
        "runs": str(STAGE9_FULL / "results"),
        "manifests": str(STAGE9_FULL / "results"),
    }
    merged["prompts"] = {
        "review_agent": _abs(config["prompts"]["review_agent"]),
        "semantic_audit": _abs(config["prompts"]["semantic_audit"]),
    }
    merged["registries"] = {
        "agent_registry": _abs(config["registries"]["agent_registry"]),
        "prompt_registry": _abs(config["registries"]["prompt_registry"]),
        "forbidden_claim_rules": _abs(config["registries"]["forbidden_claim_rules"]),
    }
    merged["audit"]["forbidden_rules_path"] = _abs(config["audit"]["forbidden_rules_path"])

    # 兼容 OrchestratorGraph 默认 RetrievalAgent（B5 实际注入 Stage9RetrievalAdapter）
    merged["retrieval"].setdefault("min_score", 1.0)
    # Prompt 输入预算默认值（Qwen vLLM max_model_len=8192 的配套约束）
    merged.setdefault("llm", {}).setdefault(
        "prompt_budget",
        {"max_input_tokens": 6000, "max_evidence_chars": 600, "max_facts": 50},
    )
    return merged
