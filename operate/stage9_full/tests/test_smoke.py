"""冒烟测试断言：1 个真实样本跑通 B0—B5，画像数字与 gold 一致。"""

from __future__ import annotations

from adapters import paths  # noqa: F401
from evaluation import metrics
from experiments import common


def test_smoke_one_sample() -> None:
    config, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split="validation", limit=1)
    assert eval_set, "无可用评估样本"
    gold_by_sample = {(str(g["sample_id"]), str(g["quarter"])): g for g in loaded["gold"].values()}

    for method in config["baselines"]:
        out = common.run_method(method, stage_config, data, eval_set[0]["card"])
        assert out.get("final_verdict") in ("PASS", "DEFER", "REJECT"), f"{method} verdict 异常"
        g = gold_by_sample.get((str(out["sample_id"]), str(out["quarter"])), {})
        row = metrics.compute_sample_metrics(out, g, tol=config["evaluation"]["numeric_tolerance"])
        assert row["numeric_accuracy"] == 1.0, f"{method} 画像数字与 gold 不一致"


if __name__ == "__main__":
    test_smoke_one_sample()
    print("test_smoke PASS")
