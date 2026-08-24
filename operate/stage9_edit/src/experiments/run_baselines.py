"""阶段9干跑入口：用少量验证集画像跑 B0-B5。

用法：
    python -m src.experiments.run_baselines --n 5 --methods B0,B1,B2,B3,B4,B5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.experiments.dataset_loader import load_profiles_with_risk
from src.experiments.paths import STAGE9_ROOT

from src.common.run_log import RunLog, new_run_dir, write_output_manifest  # noqa: E402
from src.experiments.baselines import BaselineRunner  # noqa: E402


def build_config(root: Path) -> dict:
    config = json.loads((root / "configs" / "stage9_config.json").read_text(encoding="utf-8"))
    config["paths"] = {
        "knowledge_chunks": "knowledge/chunks/regulation_chunks.jsonl",
        "standard_mapping": "knowledge/chunks/standard_document_mapping.csv",
        "whitelist": "configs/agent_profile_whitelist.json",
        "runs": "runs",
        "manifests": "manifests",
    }
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RiskProfile-Agent 阶段9 B0-B5 干跑")
    parser.add_argument("--n", type=int, default=5, help="使用的验证集画像数量")
    parser.add_argument("--split", default="validation", help="画像 split")
    parser.add_argument("--methods", default="B0,B1,B2,B3,B4,B5", help="逗号分隔的方法列表")
    parser.add_argument("--run-name", default="stage9_dryrun", help="运行目录名称")
    args = parser.parse_args(argv)

    root = STAGE9_ROOT
    config = build_config(root)
    profiles = load_profiles_with_risk(n=args.n, split=args.split, root=root)
    if not profiles:
        print(f"没有找到 split={args.split} 的画像，请检查 data/02_train_validation/。")
        return 2

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    runner = BaselineRunner(config, root)
    run_dir = new_run_dir(root / config["paths"]["runs"], args.run_name, config)
    log = RunLog(run_dir / "run_log.jsonl")
    written: dict[str, Path] = {}

    for index, profile in enumerate(profiles):
        log.log("case_start", run_id=run_dir.name, sample_id=profile.get("sample_id"), index=index)
        outputs = runner.run_profile(profile, methods)
        for method, result in outputs.items():
            log.log(
                "method_end",
                run_id=run_dir.name,
                sample_id=profile.get("sample_id"),
                method=method,
                final_verdict=result.get("final_verdict"),
                latency_ms=result.get("latency_ms"),
                model=result.get("model"),
            )
        case_path = run_dir / f"case_{index:04d}.json"
        case_path.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        written[f"case_{index:04d}.json"] = case_path

    manifest_path = write_output_manifest(run_dir, written)
    log.log("end", run_id=run_dir.name, manifest=manifest_path.name, n_cases=len(profiles))
    log.close()

    print(f"run_dir: {run_dir}")
    print(f"methods: {methods}")
    print(f"n_cases: {len(profiles)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
