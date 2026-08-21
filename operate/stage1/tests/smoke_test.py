"""阶段1冒烟测试：Schema 校验、四个模块、最小端到端、输出保护与可复现性。

运行：python tests/smoke_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError

from src.agents.audit_agent import AuditAgent
from src.agents.profile_agent import ProfileAgent
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.review_agent import ReviewAgent
from src.common.pydantic_schemas import AuditResult, ProfileCard, ReviewCard
from src.common.run_log import new_run_dir
from src.llm.client import DummyLLMClient, get_llm_client
from src.pipeline.minimal_pipeline import MinimalPipeline

FIX = ROOT / "tests" / "fixtures"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def load_json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def main() -> int:
    config = json.loads((ROOT / "configs" / "config.json").read_text(encoding="utf-8"))

    # ------------------------------------------------------------ 1. Schema
    schema_names = [
        "profile_schema.json",
        "evidence_schema.json",
        "review_card_schema.json",
        "audit_result_schema.json",
    ]
    for name in schema_names:
        try:
            json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            check(f"schema/{name} 是合法JSON", True)
        except Exception as exc:  # noqa: BLE001
            check(f"schema/{name} 是合法JSON", False, str(exc))

    profile_schema = json.loads((ROOT / "schemas" / "profile_schema.json").read_text(encoding="utf-8"))
    sample_v1 = load_json("sample_profile.json")
    sample_v2 = load_json("sample_profile_v2_mock.json")
    failure = load_json("failure_profile.json")
    retrieval_failure_query = load_json("retrieval_failure_query.json")
    review_failure_profile = load_json("review_failure_profile.json")
    audit_failure_forbidden = load_json("audit_failure_forbidden.json")
    audit_failure_future = load_json("audit_failure_future.json")

    check(
        "默认 LLM 客户端为离线 Dummy（不联网）",
        isinstance(get_llm_client(config), DummyLLMClient),
    )

    missing_required = [f for f in profile_schema["required"] if f not in sample_v1]
    check("sample_profile 覆盖 profile_schema 全部必填字段", not missing_required, str(missing_required))

    # ------------------------------------------------------------ 2. Profile Agent
    try:
        ProfileCard.model_validate(sample_v1)
        check("Pydantic 校验 sample_profile 通过", True)
    except ValidationError as exc:
        check("Pydantic 校验 sample_profile 通过", False, str(exc))

    try:
        ProfileCard.model_validate(sample_v2)
        check("Pydantic 校验 sample_profile_v2_mock 通过", True)
    except ValidationError as exc:
        check("Pydantic 校验 sample_profile_v2_mock 通过", False, str(exc))

    try:
        ProfileCard.model_validate(failure)
        check("Pydantic 拒绝 failure_profile（缺字段/越界）", False, "未抛出异常")
    except ValidationError:
        check("Pydantic 拒绝 failure_profile（缺字段/越界）", True)

    profile_agent = ProfileAgent()
    leaked = dict(sample_v1)
    leaked["label"] = 1
    leaked_out = profile_agent.run(leaked)
    leaked_fields = {f["field"] for f in leaked_out["facts"]}
    check("Profile Agent 不输出白名单外字段（label 被忽略）", "label" not in leaked_fields)

    facts_out = profile_agent.run(sample_v1)
    all_provenance = all(f["provenance"].startswith("profile:") for f in facts_out["facts"])
    check(
        "Profile Agent 输出原子事实且全部带 profile:<field> 溯源",
        facts_out["n_facts"] > 0 and all_provenance,
        f"n_facts={facts_out['n_facts']}",
    )

    # ------------------------------------------------------------ 3. Retrieval Agent
    retrieval_agent = RetrievalAgent(
        chunks_path=ROOT / config["paths"]["knowledge_chunks"],
        mapping_path=ROOT / config["paths"]["standard_mapping"],
        top_k=config["retrieval"]["top_k"],
        min_score=config["retrieval"]["min_score"],
    )
    hit = retrieval_agent.run(["1910.269"], ["R1"], query_id="q-hit")
    check("Retrieval 命中 1910.269 返回证据", len(hit.items) > 0, f"n={len(hit.items)}")
    check(
        "Retrieval 证据带 evidence_id 引用键",
        all(i.evidence_id.startswith("regulation:") for i in hit.items),
        str([i.evidence_id for i in hit.items]),
    )
    miss = retrieval_agent.run(
        retrieval_failure_query["standard_codes"],
        retrieval_failure_query["risk_categories"],
        query_id=retrieval_failure_query["query_id"],
    )
    check("Retrieval 未知标准号返回空+原因", len(miss.items) == 0 and bool(miss.empty_reason), str(miss.empty_reason))

    # ------------------------------------------------------------ 4. Review Agent
    facts_v2 = profile_agent.run(sample_v2)["facts"]
    retrieval_v2 = retrieval_agent.run(
        sample_v2.get("historical_standard_codes", []),
        sample_v2.get("historical_risk_categories", []),
        query_id=sample_v2["sample_id"],
    )
    review_agent = ReviewAgent(max_points=config["review"]["max_points"])
    draft = review_agent.run(sample_v2, facts_v2, retrieval_v2)
    points_ok = (
        1 <= len(draft["review_points"]) <= 3
        and all(p["verification_instructions_zh"] for p in draft["review_points"])
    )
    check("Review 输出≤3项复核重点且含人工核实方法", points_ok, f"n_points={len(draft['review_points'])}")

    class FakeJSONClient:
        model = "fake-qwen"

        def generate(self, messages):
            return json.dumps(
                {
                    "review_points": [
                        {
                            "point_id": "p_llm",
                            "focus_zh": "基于画像与法规证据，建议人工核对电气防护",
                            "basis_profile_facts": ["profile:history_inspections"],
                            "regulation_refs": [],
                            "missing_field_info": ["现场情况未知"],
                            "verification_instructions_zh": "核实现场",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    llm_review = ReviewAgent(max_points=3, use_llm=True, llm_client=FakeJSONClient())
    llm_draft = llm_review.run(sample_v2, facts_v2, retrieval_v2)
    check(
        "Review 走 LLM 客户端生成复核点（Qwen 路径可用）",
        llm_draft["review_points"][0]["focus_zh"] == "基于画像与法规证据，建议人工核对电气防护",
        llm_draft["review_points"][0]["focus_zh"],
    )

    # ------------------------------------------------------------ 5. Audit Agent
    audit_agent = AuditAgent(
        forbidden_patterns=config["audit"]["forbidden_patterns"],
        max_rounds=config["audit"]["max_rounds"],
    )
    audit_ok = audit_agent.run(draft, facts_v2, retrieval_v2, sample_v2)
    AuditResult.model_validate(audit_ok.model_dump())
    check("Audit 正常草稿判定通过", audit_ok.overall_verdict == "PASS", audit_ok.overall_verdict)

    audit_bad = audit_agent.run(audit_failure_forbidden, facts_v2, retrieval_v2, sample_v2)
    check("Audit 拒绝违法认定/处罚表述", audit_bad.overall_verdict == "REJECT", audit_bad.overall_verdict)

    audit_future = audit_agent.run(audit_failure_future, facts_v2, retrieval_v2, sample_v2)
    check("Audit 拒绝未来信息/事故必然性表述", audit_future.overall_verdict == "REJECT", audit_future.overall_verdict)

    # ------------------------------------------------------------ 6. 最小端到端
    pipeline = MinimalPipeline(config, ROOT)

    # Review 失败样例：证据不足必须转人工（方案中 DEFER 即转人工/HUMAN_REVIEW）
    run_review_fail = pipeline.run(review_failure_profile, run_name="review_failure")
    defer_ok = run_review_fail["final_verdict"] == "DEFER"
    check("Review 失败样例：证据不足输出 DEFER（转人工）而非生成建议", defer_ok, run_review_fail["final_verdict"])
    fallback_text = run_review_fail["card"]["review_points"][0]["focus_zh"]
    check("Review 失败样例带人工确认说明", "人工确认" in fallback_text, fallback_text)

    run1 = pipeline.run(sample_v2, run_name="e2e_v2")
    ReviewCard.model_validate(run1["card"])
    run1_card = json.loads((Path(run1["run_dir"]) / "review_card.json").read_text(encoding="utf-8"))
    check("端到端 v2 输出最终建议卡且格式校验通过", run1["final_verdict"] == "PASS", run1["final_verdict"])

    run2 = pipeline.run(sample_v2, run_name="e2e_v2")
    check("两次运行目录不同（不覆盖）", run1["run_dir"] != run2["run_dir"], f"{run1['run_dir']} vs {run2['run_dir']}")
    run2_card = json.loads((Path(run2["run_dir"]) / "review_card.json").read_text(encoding="utf-8"))
    check("同一输入两次运行建议卡完全一致（可复现）", run1_card == run2_card)

    log_lines = (Path(run1["run_dir"]) / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    log_ok = all(bool(json.loads(line)) for line in log_lines) and len(log_lines) >= 6
    check("JSONL 运行日志格式正确且包含各模块记录", log_ok, f"n_lines={len(log_lines)}")

    manifest = json.loads(Path(run1["output_manifest"]).read_text(encoding="utf-8"))
    check("输出清单包含全部输出文件的 SHA-256", len(manifest) == 5, str(list(manifest)))

    try:
        new_run_dir(
            ROOT / config["paths"]["runs"],
            "guard",
            _timestamp="20260819_000000",
            _unique="test",
        )
        new_run_dir(
            ROOT / config["paths"]["runs"],
            "guard",
            _timestamp="20260819_000000",
            _unique="test",
        )
        check("运行目录保护：重复创建拒绝覆盖", False, "第二次创建未抛错")
    except FileExistsError:
        check("运行目录保护：重复创建拒绝覆盖", True)

    # ------------------------------------------------------------ 7. 泄漏冒烟检查
    leaked_future = dict(sample_v2)
    leaked_future["future_citation_label"] = 1
    try:
        pipeline.run(leaked_future, run_name="leak_check")
        check("端到端拒绝含 future_* 字段的输入", False, "未抛出异常")
    except ValueError as exc:
        check("端到端拒绝含 future_* 字段的输入", "future_citation_label" in str(exc), str(exc))

    # 出错路径：报错必须写入 error 日志，且不生成建议卡（不覆盖已有输出）
    bad_profile = {k: v for k, v in failure.items() if k != "label"}
    try:
        pipeline.run(bad_profile, run_name="error_path")
        check("端到端失败路径记录 error 日志", False, "未抛出异常")
    except ValueError:
        run_dirs = sorted(
            (ROOT / config["paths"]["runs"]).iterdir(),
            key=lambda p: p.stat().st_mtime,
        )
        latest = run_dirs[-1] if run_dirs else None
        error_logged = False
        no_card = True
        if latest is not None:
            log_text = (latest / "run_log.jsonl").read_text(encoding="utf-8")
            error_logged = '"event": "error"' in log_text
            no_card = not (latest / "review_card.json").exists()
        check(
            "端到端失败路径记录 error 日志且不生成建议卡",
            error_logged and no_card,
            str(latest.name if latest else None),
        )

    # ------------------------------------------------------------ 汇总
    print(f"\n阶段1冒烟测试：{len(RESULTS)} 项")
    failed = 0
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        failed += 0 if ok else 1
        suffix = f"  [{detail}]" if detail else ""
        print(f"  [{mark}] {name}{suffix}")
    print(f"\n结果：{len(RESULTS) - failed}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
