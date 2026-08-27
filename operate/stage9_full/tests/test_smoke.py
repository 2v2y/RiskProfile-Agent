"""冒烟测试断言：1 个真实样本跑通 B0—B5，画像数字与 gold 一致。

离线确定性优先：默认 provider=dummy 且不初始化真实 RAG（不联网、不加载
faiss/sentence-transformers），与 canonical 单元测试保持一致；如确需在服务器
验证真实 RAG/Qwen 路径，设置 RP_STAGE9_SMOKE_ONLINE=1 后运行。
"""

from __future__ import annotations

import os

from adapters import paths  # noqa: F401
from evaluation import metrics
from experiments import common


def test_smoke_one_sample() -> None:
    config, data, stage_config = common.setup()
    if os.getenv("RP_STAGE9_SMOKE_ONLINE") != "1":
        # 离线确定性：不调用 Qwen、不加载真实 RAG；正式实验参数不受影响
        for cfg in (config, stage_config):
            cfg["llm"]["provider"] = "dummy"
            cfg["retrieval"]["use_rag"] = False
            cfg["review"]["use_llm"] = False
            cfg["semantic_audit"]["use_llm"] = False
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
