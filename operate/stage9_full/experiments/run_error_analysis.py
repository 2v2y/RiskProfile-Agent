"""错误分析：跑方法并归类错误样本，输出错误率与样本ID。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from adapters import paths  # noqa: F401
from evaluation import error_analysis
from experiments import common
from src.common.run_log import new_run_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 错误分析")
    parser.add_argument("--methods", default=None)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split=args.split, limit=args.limit)
    methods = [m.strip() for m in (args.methods or ",".join(config["baselines"])).split(",") if m.strip()]

    outputs: dict[str, list[dict]] = {}
    for m in methods:
        outputs[m] = [common.run_method(m, stage_config, data, s["card"]) for s in eval_set]
    rows = common.compute_rows(outputs, eval_set, loaded["gold"],
                               tol=config["evaluation"]["numeric_tolerance"])
    summary = error_analysis.summarize(rows)

    run_dir = new_run_dir(Path(stage_config["paths"]["runs"]) / "error_analysis",
                          "error_analysis", stage_config)
    (run_dir / "error_analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(run_dir / "error_analysis.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "sample_id", "errors"])
        for item in summary["error_samples"]:
            w.writerow([item["method"], item["sample_id"], ";".join(item["errors"])])

    print(f"错误分析完成：{len(rows)} 行，结果目录 {run_dir}")
    for method in methods:
        print(f"  {method} 错误率：",
              {k: summary["rates"].get(method, {}).get(k, 0) for k in error_analysis.ERROR_TYPES})
    return 0


if __name__ == "__main__":
    sys.exit(main())
