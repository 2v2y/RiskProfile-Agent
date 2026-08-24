"""对比实验：B0—B5 跑同一批测试样本，输出逐样本指标与汇总。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from adapters import paths  # noqa: F401
from evaluation import metrics
from experiments import common
from src.common.run_log import new_run_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 对比实验")
    parser.add_argument("--methods", default=None, help="逗号分隔，默认用 config")
    parser.add_argument("--split", default="validation", help="train/validation，默认 validation")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split=args.split, limit=args.limit)
    methods = [m.strip() for m in (args.methods or ",".join(config["baselines"])).split(",") if m.strip()]

    method_outputs: dict[str, list[dict]] = {}
    for method in methods:
        method_outputs[method] = [common.run_method(method, stage_config, data, s["card"]) for s in eval_set]

    rows = common.compute_rows(method_outputs, eval_set, loaded["gold"],
                               tol=config["evaluation"]["numeric_tolerance"])
    metric_names = config["evaluation"]["metrics"]

    out_root = Path(args.out) if args.out else Path(stage_config["paths"]["runs"]) / "comparison"
    run_dir = new_run_dir(out_root, "comparison", stage_config)

    for method in methods:
        (run_dir / method).mkdir(parents=True, exist_ok=True)
        with open(run_dir / method / "outputs.jsonl", "w", encoding="utf-8") as fh:
            for out in method_outputs[method]:
                fh.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")

    summary: dict[str, dict] = {}
    for method in methods:
        mrows = [r for r in rows if r["method"] == method]
        summary[method] = metrics.aggregate(mrows, metric_names)

    with open(run_dir / "rows.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(run_dir / "summary.csv", "w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = ["method", "n"] + metric_names + ["final_verdict_distribution"]
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for method in methods:
            row = {"method": method, "n": summary[method].get("n", 0),
                   "final_verdict_distribution": json.dumps(
                       summary[method].get("final_verdict_distribution", {}), ensure_ascii=False)}
            for m in metric_names:
                row[m] = (summary[method].get(m) or {}).get("mean", "N/A")
            w.writerow(row)

    print(f"对比实验完成：{len(eval_set)} 样本 x {len(methods)} 方法")
    print(f"结果目录：{run_dir}")
    print("\n汇总（mean）：")
    for method in methods:
        line = f"  {method}: " + ", ".join(
            f"{m}={ (summary[method].get(m) or {}).get('mean', 'N/A') }" for m in metric_names)
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
