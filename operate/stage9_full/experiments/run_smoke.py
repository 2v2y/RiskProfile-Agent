"""Smoke Test：用 1—3 个真实样本跑完整 Pipeline，验证端到端可用。

默认使用 config/experiment_config.json 的 llm.provider（正式运行=qwen）。
离线验证可用 --provider dummy：不调用 Qwen，用确定性假模型跑通数据流。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from adapters import paths  # noqa: F401
from adapters import validator
from experiments import common
from src.llm.client import get_llm_call_log, reset_llm_call_log  # noqa: E402
from src.common.run_log import new_run_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段9 Smoke Test")
    parser.add_argument("--n", type=int, default=2)
    parser.add_argument("--split", default=None)
    parser.add_argument(
        "--provider", default=None,
        help="覆盖 llm.provider（qwen/dummy）；默认用 config。",
    )
    parser.add_argument(
        "--require-qwen", action="store_true",
        help="provider=qwen 时若没有任何真实 Qwen 调用则失败（防止静默跑假模型）。",
    )
    args = parser.parse_args(argv)

    config, data, stage_config = common.setup()
    if args.provider:
        config["llm"]["provider"] = args.provider
        stage_config["llm"]["provider"] = args.provider
        print(f"[INFO] llm.provider 覆盖为 {args.provider}")
    effective_provider = os.getenv("RP_LLM_PROVIDER") or stage_config["llm"].get("provider", "dummy")
    print(f"[INFO] 实际生效 provider = {effective_provider}"
          + ("" if effective_provider == stage_config["llm"].get("provider")
             else f"（环境变量覆盖 config 的 {stage_config['llm'].get('provider')!r}）"))
    if args.provider == "dummy":
        # 离线确定性冒烟：同时关闭真实 RAG 与 LLM 审查，避免依赖服务器模型。
        for cfg in (config, stage_config):
            cfg["retrieval"]["use_rag"] = False
            cfg["review"]["use_llm"] = False
            cfg["semantic_audit"]["use_llm"] = False
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
    llm_records: list[dict] = []
    for method in methods:
        outputs[method] = []
        for s in eval_set:
            reset_llm_call_log()
            out = common.run_method(method, stage_config, data, s["card"])
            calls = get_llm_call_log()
            out["qwen_attempts"] = len(calls)
            out["qwen_calls_ok"] = sum(1 for c in calls if c.get("success"))
            for c in calls:
                c = dict(c)
                c["method"] = method
                c["sample_id"] = out.get("sample_id")
                llm_records.append(c)
            outputs[method].append(out)

    print("\nSmoke Test 结果（每方法 x 样本）：")
    ok = True
    for method in methods:
        for out in outputs[method]:
            verdict = out.get("final_verdict")
            n_ev = len((out.get("retrieval") or {}).get("items", []))
            qwen = out.get("qwen_calls_ok", 0)
            llm_src = (out.get("draft_review") or {}).get("llm_source", "n/a")
            print(
                f"  {method} sample={out.get('sample_id')} verdict={verdict} "
                f"evidence={n_ev} qwen_ok={qwen} llm_source={llm_src}"
            )
            if verdict not in ("PASS", "DEFER", "REJECT"):
                ok = False

    print("\nQwen 真实调用统计（成功次数）：")
    total_ok = 0
    for method in methods:
        n_ok = sum(o.get("qwen_calls_ok", 0) for o in outputs[method])
        total_ok += n_ok
        print(f"  {method}: {n_ok}")
    print(f"  合计: {total_ok}")
    if effective_provider == "qwen" and total_ok == 0:
        print("[FAIL] provider=qwen 但没有任何真实 Qwen 调用（可能被环境变量/RAG空证据/回退影响）。")
        ok = False
    if args.require_qwen and effective_provider != "qwen":
        print(f"[FAIL] --require-qwen 但实际 provider={effective_provider}。")
        ok = False
    if args.require_qwen and effective_provider == "qwen" and total_ok == 0:
        print("[FAIL] --require-qwen 但真实 Qwen 调用数为 0。")
        ok = False

    run_dir = new_run_dir(Path(stage_config["paths"]["runs"]), "smoke_test", stage_config)
    (run_dir / "cards.json").write_text(
        json.dumps([s["card"] for s in eval_set], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "outputs.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "llm_calls.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in llm_records),
        encoding="utf-8")
    (run_dir / "llm_summary.json").write_text(
        json.dumps(
            {
                "effective_provider": effective_provider,
                "total_qwen_calls_ok": total_ok,
                "per_method": {
                    m: sum(o.get("qwen_calls_ok", 0) for o in outputs[m]) for m in methods
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8")
    print(f"\n结果目录：{run_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
