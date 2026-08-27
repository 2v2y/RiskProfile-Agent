"""LLM context budget 最小离线测试（不调用 Qwen、不联网、不加载模型）。

验证：
  1. 超长输入会被正确裁剪/压缩，估算 input + output <= 8192；
  2. 正常短输入不会被错误裁剪；
  3. 裁剪后 JSON 输出格式不受影响（review_points 仍可解析）；
  4. 客户端统一闸门对任意超长 user 内容都能硬性压到预算内。

运行：
    python -m tests.test_prompt_budget
    或 pytest tests/test_prompt_budget.py
"""

from __future__ import annotations

import json
import sys

from src.common import prompt_budget as pb

MAX_CONTEXT = 8192
OUTPUT = 1024


def _huge_messages() -> list[dict[str, str]]:
    """构造超长输入（模拟生产构造端已压缩，再交给客户端闸门）：3 条超长证据 + 60 条事实。"""
    evidence = [
        {
            "evidence_id": f"regulation:DOC-{i}#s",
            "document_id": f"DOC-{i}",
            "standard_number": "1910.132",
            "section": "s",
            "text": "法规正文" * 1500,  # 4500 字符
            "source_url": "https://example.invalid/x",
            "score": 0.9 - i * 0.1,
        }
        for i in range(3)
    ]
    facts = [
        {
            "fact_id": f"fact_{i}",
            "statement_zh": "画像字段说明" * 50,
            "field": f"field_{i}",
            "value": "value" * 100,
            "provenance": f"profile:field_{i}",
        }
        for i in range(60)
    ]
    profile = {"sample_id": "S1", "quarter": "2024Q1", "score_evidence": "长文本" * 500}
    user = json.dumps(
        {
            "profile": pb.compact_profile(profile),
            "profile_facts": pb.compact_facts(facts),
            "evidence": pb.compact_evidence(evidence),
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": "你是复核建议生成器。" * 100},
        {"role": "user", "content": user},
    ]


def test_overlong_input_trimmed_within_budget() -> None:
    """1) 超长输入必须被裁剪到 input + output <= 8192，且证据正文被截断。"""
    messages = _huge_messages()
    _, report = pb.prepare_messages(
        messages,
        max_input_tokens=6000,
        max_context_tokens=MAX_CONTEXT,
        output_tokens=OUTPUT,
    )
    assert report["estimated_total"] <= MAX_CONTEXT, report
    assert report["after_tokens"] <= report["available_input_tokens"], report
    assert report["trimmed"], "超长输入应发生裁剪"
    user = json.loads(messages[1]["content"])
    for it in user["evidence"]:
        assert len(it["text"]) <= 300, "证据正文应被截断到 300 字符"
    assert len(user["profile_facts"]) <= 30, "事实条数应被收缩"


def test_short_input_untouched() -> None:
    """2) 正常短输入不得被错误裁剪。"""
    messages = [
        {"role": "system", "content": "短 system"},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "画像事实": [{"field": "history_inspections", "value": 3}],
                    "法规证据": [{"evidence_id": "r:1", "text": "short"}],
                    "证据不足原因": None,
                },
                ensure_ascii=False,
            ),
        },
    ]
    original = [dict(m) for m in messages]
    _, report = pb.prepare_messages(messages, max_input_tokens=6000, output_tokens=OUTPUT)
    assert report["trimmed"] == [], report
    assert messages == original, "短输入不应被修改"
    assert report["after_tokens"] + OUTPUT <= MAX_CONTEXT


def test_json_output_unaffected_after_trim() -> None:
    """3) 裁剪后 LLM 返回的 JSON 仍能被 ReviewAgent 正常解析。"""
    from src.agents.review_agent import ReviewAgent
    from src.common.pydantic_schemas import EvidenceItem, RetrievalResult

    class _Capture:
        model = "capture"

        def __init__(self):
            self.messages = None

        def generate(self, messages):
            self.messages = messages
            return json.dumps(
                {
                    "review_points": [
                        {
                            "point_id": "point_1",
                            "focus_zh": "建议人工核对现场防护措施",
                            "basis_profile_facts": ["profile:history_inspections"],
                            "regulation_refs": ["regulation:DOC-0#s"],
                            "missing_field_info": ["现场情况未知"],
                            "verification_instructions_zh": "调取现场记录核实",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    capture = _Capture()
    review = ReviewAgent(
        max_points=3,
        model="budget-test",
        llm_client=capture,
        use_llm=True,
        fail_on_llm_error=True,
    )
    evidence = [
        EvidenceItem(
            evidence_id="regulation:DOC-0#s",
            document_id="DOC-0",
            standard_number="1910.132",
            section="s",
            text="法规正文" * 1500,
            source_type="regulation",
            source_url="https://example.invalid/x",
            retrieved_at="2026-08-26",
            score=0.9,
        )
    ]
    retrieval = RetrievalResult(
        query_id="q1", standard_number="1910.132", risk_categories=[], items=evidence
    )
    facts = [
        {"field": "history_inspections", "value": 3, "provenance": "profile:history_inspections"}
    ]
    draft = review.run(
        {"sample_id": "S1", "quarter": "2024Q1", "ranking_cutoff": "2024-03-31"},
        facts,
        retrieval,
    )
    assert draft["review_points"], "裁剪后 JSON 解析不应受影响"
    assert draft["review_points"][0]["focus_zh"] == "建议人工核对现场防护措施"
    assert capture.messages is not None
    # 客户端闸门同样覆盖该路径
    _, report = pb.prepare_messages(capture.messages, output_tokens=OUTPUT)
    assert report["estimated_total"] <= MAX_CONTEXT, report


def test_client_hard_guard_non_json_content() -> None:
    """4) 任意超长 user 内容（含非 JSON）也必须被硬性压到预算内。"""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 30000},
    ]
    _, report = pb.prepare_messages(
        messages, max_input_tokens=6000, output_tokens=OUTPUT, max_context_tokens=MAX_CONTEXT
    )
    assert report["estimated_total"] <= MAX_CONTEXT, report
    assert any("user_content_hard_cap" in t for t in report["trimmed"]), report
    assert messages[1]["content"].endswith("…[已按 context budget 硬截断]")


def main() -> int:
    test_overlong_input_trimmed_within_budget()
    test_short_input_untouched()
    test_json_output_unaffected_after_trim()
    test_client_hard_guard_non_json_content()
    print("test_prompt_budget PASS (4 tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
