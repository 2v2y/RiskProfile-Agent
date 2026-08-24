"""实验共享工具：加载数据、构建评价集、跑方法、算指标。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adapters import data_loader
from adapters import paths
from baselines.base import get_baseline
from evaluation import metrics


def setup() -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
    config = paths.load_experiment_config()
    data = paths.resolve_data_paths(config)
    stage_config = paths.build_stage_config(config, data)
    return config, data, stage_config


def load_everything(data: dict[str, Path]) -> dict[str, Any]:
    profiles = data_loader.load_profiles(data)
    cases = data_loader.load_benchmark_cases(data)
    gold = data_loader.load_gold(data)
    return {"profiles": profiles, "cases": cases, "gold": gold}


def build_evaluation_set(loaded: dict[str, Any], split: str | None = None,
                         limit: int | None = None) -> list[dict[str, Any]]:
    profiles = loaded["profiles"]
    out: list[dict[str, Any]] = []
    for case in loaded["cases"]:
        if split is not None and case.get("split") != split:
            continue
        key = (str(case.get("sample_id")), str(case.get("quarter")))
        card = profiles.get(key)
        if card is None:
            continue
        out.append({"case": case, "card": card})
        if limit is not None and len(out) >= limit:
            break
    return out


def run_method(method: str, stage_config: dict[str, Any], data: dict[str, Path],
               card: dict[str, Any]) -> dict[str, Any]:
    return get_baseline(method, stage_config, data).run(card)


def compute_rows(method_outputs: dict[str, list[dict[str, Any]]],
                 eval_set: list[dict[str, Any]], gold: dict[str, dict[str, Any]],
                 tol: float = 0.001) -> list[dict[str, Any]]:
    gold_by_sample = {(str(g["sample_id"]), str(g["quarter"])): g for g in gold.values()}
    rows: list[dict[str, Any]] = []
    for method, outputs in method_outputs.items():
        for out in outputs:
            g = gold_by_sample.get((str(out.get("sample_id")), str(out.get("quarter"))), {})
            rows.append(metrics.compute_sample_metrics(out, g, tol))
    return rows
