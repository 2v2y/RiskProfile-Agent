"""消融实验：完整系统 vs 去掉某模块的变体，看各指标变化。"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from adapters import paths  # noqa: F401
from evaluation import bootstrap, metrics
from experiments import common
from src.common.run_log import new_run_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 消融实验")
    parser.add_argument("--split", default="validation")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split=args.split, limit=args.limit)

    full_method = config["ablation"]["full"]
    variant_specs = config["ablation"]["variants"]
    methods = list(dict.fromkeys([full_method] + [v["base"] for v in variant_specs]))

    outputs: dict[str, list[dict]] = {}
    for m in methods:
        outputs[m] = [common.run_method(m, stage_config, data, s["card"]) for s in eval_set]
    rows = common.compute_rows(outputs, eval_set, loaded["gold"],
                               tol=config["evaluation"]["numeric_tolerance"])
    by_method = {m: [r for r in rows if r["method"] == m] for m in methods}

    full_rows = by_method[full_method]
    metric_report = ["numeric_accuracy", "citation_correctness",
                     "unsupported_claim", "traceability", "safe_refusal"]

    run_dir = new_run_dir(Path(stage_config["paths"]["runs"]) / "ablation", "ablation", stage_config)
    csv_path = run_dir / "ablation_summary.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["comparison", "metric", "full_mean", "variant_mean", "diff",
                    "ci_lower", "ci_upper", "n_pairs"])
        for spec in variant_specs:
            vrows = by_method[spec["base"]]
            for m in metric_report:
                fm = metrics.aggregate(full_rows, [m]).get(m, {}).get("mean", float("nan"))
                vm = metrics.aggregate(vrows, [m]).get(m, {}).get("mean", float("nan"))
                ci = bootstrap.paired_bootstrap(
                    full_rows, vrows, m,
                    n_resamples=config["evaluation"]["bootstrap"]["n_resamples"],
                    confidence=config["evaluation"]["bootstrap"]["confidence"],
                    seed=config["evaluation"]["random_seed"],
                )
                w.writerow([f"{full_method} vs {spec['name']}", m, fm, vm,
                            round(fm - vm, 6), ci.get("ci_lower", "N/A"),
                            ci.get("ci_upper", "N/A"), ci.get("n_pairs", 0)])

    print(f"消融实验完成：{len(eval_set)} 样本，基线 {full_method}，变体 {len(variant_specs)} 个")
    print(f"结果：{csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
