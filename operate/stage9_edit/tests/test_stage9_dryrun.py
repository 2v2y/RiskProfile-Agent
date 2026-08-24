"""阶段9离线干跑测试：不依赖 Qwen 和 FAISS。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.experiments.baselines import BaselineRunner, FakeLLMClient  # noqa: E402
from src.experiments.dataset_loader import load_profiles_with_risk  # noqa: E402
from src.experiments.run_baselines import build_config  # noqa: E402


def main() -> int:
    config = build_config(ROOT)
    profiles = load_profiles_with_risk(n=2, split="validation", root=ROOT)
    if not profiles:
        print("没有验证集画像，跳过干跑")
        return 0

    runner = BaselineRunner(config, ROOT)
    fake = FakeLLMClient()
    runner.llm_client = fake
    runner.review_llm.llm_client = fake
    runner.semantic_agent.llm_client = fake

    methods = ["B0", "B1", "B2", "B3", "B4", "B5"]
    outputs = runner.run_profile(profiles[0], methods)
    ok = all(
        m in outputs and outputs[m].get("final_verdict") in ("PASS", "DEFER", "REJECT")
        for m in methods
    )
    print("methods:", sorted(outputs))
    print("verdicts:", {m: outputs[m]["final_verdict"] for m in outputs})
    print("dryrun ok:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
