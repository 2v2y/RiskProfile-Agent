"""Smoke Test：用 1—3 个真实样本跑完整 Pipeline，验证端到端可用。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapters import paths  # noqa: F401
from adapters import validator
from experiments import common
from src.common.run_log import new_run_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 Smoke Test")
    parser.add_argument("--n", type=int, default=2)
    parser.add_argument("--split", default=None)
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split=args.split, limit=args.n)
    if not eval_set:
        print("没有可用的评估样本，请检查数据路径。")
        return 2

    report = validator.validate_cards(loaded["profiles"])
    print(f"画像契约校验：总 {report['total']}，通过 {report['passed']}，失败 {report['failed']}")
    if report["failed"]:
        for f in report["failures"][:10]:
            print("  FAIL", f["sample_id"], f["quarter"], f["error"][:160])
        return 1

    methods = config["baselines"]
    outputs: dict[str, list[dict]] = {}
    for method in methods:
        outputs[method] = [common.run_method(method, stage_config, data, s["card"]) for s in eval_set]

    print("\nSmoke Test 结果（每方法 x 样本）：")
    ok = True
    for method in methods:
        for out in outputs[method]:
            verdict = out.get("final_verdict")
            n_ev = len((out.get("retrieval") or {}).get("items", []))
            print(f"  {method} sample={out.get('sample_id')} verdict={verdict} evidence={n_ev}")
            if verdict not in ("PASS", "DEFER", "REJECT"):
                ok = False

    run_dir = new_run_dir(Path(stage_config["paths"]["runs"]), "smoke_test", stage_config)
    (run_dir / "cards.json").write_text(
        json.dumps([s["card"] for s in eval_set], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "outputs.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果目录：{run_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
