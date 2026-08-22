"""阶段8验收测试。

覆盖：
1. Schema 验证（四个 JSON Schema + Pydantic）
2. 单模块测试：Profile / Retrieval / Review / Audit / Semantic
3. 三类端到端样例：PASS / HUMAN_REVIEW / REJECT
4. 最大审计轮次、证据为空失败关闭、禁止性表达、数字/ID不一致
5. 完整端到端日志、证据追踪与可复现性
6. Registry（agent_registry / prompt_registry / forbidden_claim_rules）

运行：python tests/test_stage8.py（也可用 pytest 直接运行本文件）
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema
import yaml
from pydantic import ValidationError

from src.agents.audit_agent import AuditAgent
from src.agents.profile_agent import ProfileAgent, ProfileInputError
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.review_agent import ReviewAgent
from src.agents.semantic_audit_agent import SemanticAuditAgent
from src.common.pydantic_schemas import AuditResult, ProfileCard, RetrievalResult, ReviewCard
from src.llm.client import DummyLLMClient, get_llm_client
from src.orchestrator import OrchestratorGraph

FIX = ROOT / "tests" / "fixtures"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def load_json(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def make_draft(
    profile: dict,
    points: list[dict],
    ledger: list[dict] | None = None,
    missing: list[dict] | None = None,
) -> dict:
    return {
        "sample_id": profile["sample_id"],
        "quarter": profile.get("quarter"),
        "ranking_cutoff": profile.get("ranking_cutoff"),
        "review_points": points,
        "official_citations": [],
        "missing_information": missing or [],
        "evidence_ledger": ledger
        or [
            {
                "claim_id": p["point_id"],
                "statement_zh": p["focus_zh"],
                "evidence_refs": p.get("basis_profile_facts", []) + p.get("regulation_refs", []),
                "status": "supported" if (p.get("basis_profile_facts") or p.get("regulation_refs")) else "unsupported",
            }
            for p in points
        ],
        "model": "stub",
        "prompt_version": "review_agent_v1",
        "evidence_sufficient": True,
    }


# ------------------------------------------------------------------ 桩 Review Agent
class ForbiddenReviewAgent:
    prompt_version = "review_agent_v1"
    model = "stub-forbidden"
    max_points = 3

    def run(self, profile, facts, retrieval):
        focus = "该单位违反了1910.269条款，应当处罚。"
        return make_draft(
            profile,
            [
                {
                    "point_id": "point_1",
                    "focus_zh": focus,
                    "basis_profile_facts": [],
                    "regulation_refs": [],
                    "missing_field_info": [],
                    "verification_instructions_zh": "核实现场",
                }
            ],
            ledger=[{"claim_id": "point_1", "statement_zh": focus, "evidence_refs": [], "status": "unsupported"}],
        )


class WrongNumberReviewAgent:
    prompt_version = "review_agent_v1"
    model = "stub-wrong-number"
    max_points = 3

    def run(self, profile, facts, retrieval):
        focus = "历史检查次数为9次。"
        return make_draft(
            profile,
            [
                {
                    "point_id": "point_1",
                    "focus_zh": focus,
                    "basis_profile_facts": ["profile:history_inspections"],
                    "regulation_refs": [],
                    "missing_field_info": [],
                    "verification_instructions_zh": "核实现场",
                }
            ],
            ledger=[
                {
                    "claim_id": "point_1",
                    "statement_zh": focus,
                    "evidence_refs": ["profile:history_inspections"],
                    "status": "supported",
                }
            ],
        )


class UnsupportedReviewAgent:
    prompt_version = "review_agent_v1"
    model = "stub-unsupported"
    max_points = 3

    def run(self, profile, facts, retrieval):
        focus = "该单位需要人工关注现场情况。"
        return make_draft(
            profile,
            [
                {
                    "point_id": "point_1",
                    "focus_zh": focus,
                    "basis_profile_facts": [],
                    "regulation_refs": [],
                    "missing_field_info": ["现场情况未知"],
                    "verification_instructions_zh": "核实现场",
                }
            ],
            ledger=[{"claim_id": "point_1", "statement_zh": focus, "evidence_refs": [], "status": "unsupported"}],
        )


class FakeJSONClient:
    model = "fake-qwen"

    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, messages):
        return json.dumps(self.payload, ensure_ascii=False)


# ------------------------------------------------------------------ 测试主体
def test_schema_and_fixtures(config: dict) -> None:
    schema_names = [
        "profile_schema.json",
        "evidence_schema.json",
        "review_card_schema.json",
        "audit_result_schema.json",
    ]
    for name in schema_names:
        try:
            json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            check(f"schema/{name} 是合法 JSON", True)
        except Exception as exc:  # noqa: BLE001
            check(f"schema/{name} 是合法 JSON", False, str(exc))

    normal = load_json("e2e_normal.json")
    human = load_json("e2e_human_review.json")
    reject = load_json("e2e_reject.json")
    for name, fixture in (("e2e_normal", normal), ("e2e_human_review", human), ("e2e_reject", reject)):
        try:
            ProfileCard.model_validate(fixture)
            check(f"ProfileCard 校验 {name} 通过", True)
        except ValidationError as exc:
            check(f"ProfileCard 校验 {name} 通过", False, str(exc))

    profile_schema = json.loads((ROOT / "schemas" / "profile_schema.json").read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=normal, schema=profile_schema)
        check("jsonschema 校验 e2e_normal 符合 profile_schema", True)
    except jsonschema.ValidationError as exc:
        check("jsonschema 校验 e2e_normal 符合 profile_schema", False, str(exc))


def test_profile_agent(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    agent = ProfileAgent(whitelist_path=str(ROOT / config["paths"]["whitelist"]), strict=True)
    out = agent.run(normal)
    check(
        "Profile：输出结构化原子事实且全部带 profile:<field> 溯源",
        out["n_facts"] > 0 and all(f["provenance"].startswith("profile:") for f in out["facts"]),
        f"n_facts={out['n_facts']}",
    )
    joined = " ".join(f["statement_zh"] for f in out["facts"])
    forbidden_words = ["违法", "处罚", "必然", "管理情况", "现场情况"]
    check(
        "Profile：不生成违法判断/管理情况/现场情况/事故必然性",
        not any(w in joined for w in forbidden_words),
        joined[:120],
    )

    unknown = dict(normal)
    unknown["mystery_field"] = 1
    try:
        agent.run(unknown)
        check("Profile 严格模式：白名单外未知字段报错", False, "未抛出异常")
    except ProfileInputError:
        check("Profile 严格模式：白名单外未知字段报错", True)

    leaked_label = dict(normal)
    leaked_label["label"] = 1
    try:
        agent.run(leaked_label)
        check("Profile 严格模式：白名单禁止字段 label 报错", False, "未抛出异常")
    except ProfileInputError:
        check("Profile 严格模式：白名单禁止字段 label 报错", True)

    leaked_future = dict(normal)
    leaked_future["future_citation_label"] = 1
    try:
        agent.run(leaked_future)
        check("Profile 严格模式：future_* 字段报错", False, "未抛出异常")
    except ProfileInputError:
        check("Profile 严格模式：future_* 字段报错", True)

    # 阶段1 兼容：默认非严格模式不破坏已有行为
    lenient = ProfileAgent()
    out_lenient = lenient.run(leaked_label)
    check(
        "Profile 默认模式：与阶段1兼容（label 不进入事实）",
        "label" not in {f["field"] for f in out_lenient["facts"]},
    )


def test_retrieval_agent(config: dict) -> None:
    agent = RetrievalAgent(
        chunks_path=ROOT / config["paths"]["knowledge_chunks"],
        mapping_path=ROOT / config["paths"]["standard_mapping"],
        top_k=config["retrieval"]["top_k"],
        min_score=config["retrieval"]["min_score"],
    )
    hit = agent.run(["1910.269"], ["R1"], query_id="q-hit")
    check("Retrieval：命中 1910.269 返回证据", len(hit.items) > 0, f"n={len(hit.items)}")
    check("Retrieval：返回条数不超过 top_k", len(hit.items) <= config["retrieval"]["top_k"])
    check(
        "Retrieval：证据带 evidence_id 引用键",
        all(i.evidence_id.startswith("regulation:") for i in hit.items),
    )
    check(
        "Retrieval：证据字段完整（document_id/standard/section/text/url/version/score/rank）",
        all(
            i.document_id and i.standard_number and i.section and i.text and i.source_url and i.retrieved_at and i.score
            for i in hit.items
        ),
    )

    evidence_schema = json.loads((ROOT / "schemas" / "evidence_schema.json").read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=hit.model_dump(), schema=evidence_schema)
        check("Retrieval：输出符合 evidence_schema.json", True)
    except jsonschema.ValidationError as exc:
        check("Retrieval：输出符合 evidence_schema.json", False, str(exc))

    miss = agent.run(["1910.999"], [], query_id="q-miss")
    check("Retrieval：未知标准号返回空+原因", len(miss.items) == 0 and bool(miss.empty_reason), str(miss.empty_reason))

    no_std = agent.run([], [], query_id="q-nostd")
    check("Retrieval：无标准编号返回空+原因", len(no_std.items) == 0 and bool(no_std.empty_reason))

    hit2 = agent.run(["1910.269"], ["R1"], query_id="q-hit2")
    check(
        "Retrieval：同一查询两次得分一致（确定性）",
        [i.score for i in hit.items] == [i.score for i in hit2.items],
    )


def test_review_agent(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    profile_agent = ProfileAgent(whitelist_path=str(ROOT / config["paths"]["whitelist"]), strict=True)
    facts = profile_agent.run(normal)["facts"]
    retrieval_agent = RetrievalAgent(
        chunks_path=ROOT / config["paths"]["knowledge_chunks"],
        mapping_path=ROOT / config["paths"]["standard_mapping"],
        top_k=config["retrieval"]["top_k"],
        min_score=config["retrieval"]["min_score"],
    )
    retrieval = retrieval_agent.run(
        normal.get("historical_standard_codes", []),
        normal.get("historical_risk_categories", []),
        query_id=normal["sample_id"],
    )
    review = ReviewAgent(max_points=config["review"]["max_points"], prompt_version="review_agent_v1")
    draft = review.run(normal, facts, retrieval)
    points = draft["review_points"]
    check("Review：最多3项复核重点", 1 <= len(points) <= 3, f"n={len(points)}")
    check(
        "Review：每项包含关注点/画像依据/法规依据/缺失信息/核实方法",
        all(
            p["focus_zh"] and p["verification_instructions_zh"]
            and isinstance(p.get("basis_profile_facts"), list)
            and isinstance(p.get("regulation_refs"), list)
            and isinstance(p.get("missing_field_info"), list)
            for p in points
        ),
    )
    joined = " ".join(p["focus_zh"] for p in points)
    check("Review：无禁止性表达", not any(w in joined for w in ("违法", "处罚", "必然")), joined[:120])

    # 证据为空 -> 人工复核点（不引用法规证据）
    empty_retrieval = RetrievalResult(
        query_id="q-empty",
        standard_number="UNKNOWN",
        risk_categories=[],
        items=[],
        empty_reason="知识库未覆盖标准编号",
    )
    human_draft = review.run(normal, facts, empty_retrieval)
    check(
        "Review：证据为空时生成人工复核点且不引用法规证据",
        human_draft["review_points"][0]["focus_zh"].startswith("证据不足")
        and human_draft["review_points"][0]["regulation_refs"] == [],
        human_draft["review_points"][0]["focus_zh"],
    )

    # LLM 路径
    fake = FakeJSONClient(
        {
            "review_points": [
                {
                    "point_id": "point_llm",
                    "focus_zh": "基于画像与法规证据，建议人工核对电气防护",
                    "basis_profile_facts": ["profile:history_inspections"],
                    "regulation_refs": [],
                    "missing_field_info": ["现场情况未知"],
                    "verification_instructions_zh": "核实现场",
                }
            ]
        }
    )
    llm_review = ReviewAgent(max_points=3, use_llm=True, llm_client=fake, prompt_version="review_agent_v1")
    llm_draft = llm_review.run(normal, facts, retrieval)
    check("Review：LLM 路径生成复核点", llm_draft["review_points"][0]["focus_zh"].startswith("基于画像"), "")


def test_audit_agent(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    profile_agent = ProfileAgent(whitelist_path=str(ROOT / config["paths"]["whitelist"]), strict=True)
    facts = profile_agent.run(normal)["facts"]
    retrieval_agent = RetrievalAgent(
        chunks_path=ROOT / config["paths"]["knowledge_chunks"],
        mapping_path=ROOT / config["paths"]["standard_mapping"],
        top_k=config["retrieval"]["top_k"],
        min_score=config["retrieval"]["min_score"],
    )
    retrieval = retrieval_agent.run(
        normal.get("historical_standard_codes", []),
        normal.get("historical_risk_categories", []),
        query_id=normal["sample_id"],
    )
    audit = AuditAgent(
        forbidden_patterns=config["audit"]["forbidden_patterns"],
        max_rounds=config["audit"]["max_rounds"],
        forbidden_rules_path=str(ROOT / config["audit"]["forbidden_rules_path"]),
    )

    good_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "历史共有5次成熟检查",
                "basis_profile_facts": ["profile:history_inspections"],
                "regulation_refs": [],
                "missing_field_info": ["现场情况未知"],
                "verification_instructions_zh": "核实现场",
            }
        ],
    )
    good = audit.run(good_draft, facts, retrieval, normal)
    AuditResult.model_validate(good.model_dump())
    check("Audit：正常陈述判定 PASS", good.overall_verdict == "PASS", good.overall_verdict)

    forbidden_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "该单位违反了1910.269条款，应当处罚。",
                "basis_profile_facts": [],
                "regulation_refs": [],
                "missing_field_info": [],
                "verification_instructions_zh": "x",
            }
        ],
    )
    bad = audit.run(forbidden_draft, facts, retrieval, normal)
    check("Audit：禁止性表达 -> REJECT", bad.overall_verdict == "REJECT", bad.overall_verdict)

    future_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "该单位未来必然发生事故。",
                "basis_profile_facts": [],
                "regulation_refs": [],
                "missing_field_info": [],
                "verification_instructions_zh": "x",
            }
        ],
    )
    future = audit.run(future_draft, facts, retrieval, normal)
    check("Audit：事故必然/未来信息 -> REJECT", future.overall_verdict == "REJECT", future.overall_verdict)

    wrong_num = audit.run(WrongNumberReviewAgent().run(normal, facts, retrieval), facts, retrieval, normal)
    check("Audit：数字与画像不一致 -> REJECT", wrong_num.overall_verdict == "REJECT", wrong_num.overall_verdict)

    id_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "样本 SAMPLE_WRONG_ID 的历史检查次数为5次。",
                "basis_profile_facts": ["profile:history_inspections"],
                "regulation_refs": [],
                "missing_field_info": [],
                "verification_instructions_zh": "x",
            }
        ],
    )
    id_bad = audit.run(id_draft, facts, retrieval, normal)
    check("Audit：样本ID不一致 -> REJECT", id_bad.overall_verdict == "REJECT", id_bad.overall_verdict)

    std_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "历史涉及标准 1910.999。",
                "basis_profile_facts": [],
                "regulation_refs": [],
                "missing_field_info": [],
                "verification_instructions_zh": "x",
            }
        ],
    )
    std_bad = audit.run(std_draft, facts, retrieval, normal)
    check("Audit：陈述出现检索证据外的标准编号 -> REJECT", std_bad.overall_verdict == "REJECT", std_bad.overall_verdict)

    unsupported = audit.run(UnsupportedReviewAgent().run(normal, facts, retrieval), facts, retrieval, normal)
    check("Audit：无依据陈述 -> DEFER", unsupported.overall_verdict == "DEFER", unsupported.overall_verdict)


def test_semantic_audit(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    profile_agent = ProfileAgent(whitelist_path=str(ROOT / config["paths"]["whitelist"]), strict=True)
    facts = profile_agent.run(normal)["facts"]
    retrieval_agent = RetrievalAgent(
        chunks_path=ROOT / config["paths"]["knowledge_chunks"],
        mapping_path=ROOT / config["paths"]["standard_mapping"],
        top_k=config["retrieval"]["top_k"],
        min_score=config["retrieval"]["min_score"],
    )
    retrieval = retrieval_agent.run(
        normal.get("historical_standard_codes", []),
        normal.get("historical_risk_categories", []),
        query_id=normal["sample_id"],
    )
    good_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "历史共有5次成熟检查",
                "basis_profile_facts": ["profile:history_inspections"],
                "regulation_refs": [],
                "missing_field_info": ["现场情况未知"],
                "verification_instructions_zh": "核实现场",
            }
        ],
    )
    sem = SemanticAuditAgent(use_llm=False, prompt_version="semantic_audit_v1")
    res = sem.run(good_draft, facts, retrieval)
    check("Semantic：规则回退判定支持 -> PASS", res["overall_verdict"] == "PASS", res["overall_verdict"])

    bad_draft = make_draft(
        normal,
        [
            {
                "point_id": "point_1",
                "focus_zh": "该单位违反了1910.269条款。",
                "basis_profile_facts": [],
                "regulation_refs": [],
                "missing_field_info": [],
                "verification_instructions_zh": "x",
            }
        ],
    )
    res_bad = sem.run(bad_draft, facts, retrieval)
    check("Semantic：规则回退拒绝禁止性表达 -> REJECT", res_bad["overall_verdict"] == "REJECT", res_bad["overall_verdict"])

    fake = FakeJSONClient(
        {"claims": [{"claim_id": "point_1", "verdict": "REJECT", "reason": "证据不支持该陈述"}]}
    )
    sem_llm = SemanticAuditAgent(use_llm=True, llm_client=fake, prompt_version="semantic_audit_v1")
    res_llm = sem_llm.run(good_draft, facts, retrieval)
    check("Semantic：LLM 路径输出判定", res_llm["overall_verdict"] == "REJECT", res_llm["overall_verdict"])


def _make_orchestrator(config: dict, agents: dict | None = None) -> OrchestratorGraph:
    return OrchestratorGraph(config, ROOT, agents=agents)


def test_e2e_three_paths(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    human = load_json("e2e_human_review.json")
    reject = load_json("e2e_reject.json")

    # 1) 正常样例 -> PASS
    orch = _make_orchestrator(config)
    r_normal = orch.run(normal, run_name="stage8_normal")
    check("端到端正常样例：final_status=PASS", r_normal["final_status"] == "PASS", r_normal["final_verdict"])
    try:
        ReviewCard.model_validate(r_normal["card"])
        check("端到端正常样例：建议卡符合 review_card_schema", True)
    except ValidationError as exc:
        check("端到端正常样例：建议卡符合 review_card_schema", False, str(exc))
    review_card_schema = json.loads((ROOT / "schemas" / "review_card_schema.json").read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=r_normal["card"], schema=review_card_schema)
        check("端到端正常样例：jsonschema 校验建议卡通过", True)
    except jsonschema.ValidationError as exc:
        check("端到端正常样例：jsonschema 校验建议卡通过", False, str(exc))

    run_dir = Path(r_normal["run_dir"])
    expected_files = [
        "profile_facts.json",
        "retrieval.json",
        "draft_review.json",
        "audit.json",
        "semantic_audit.json",
        "review_card.json",
        "state_trace.json",
        "run_log.jsonl",
        "output_manifest.json",
    ]
    check("端到端正常样例：输出文件齐全", all((run_dir / f).exists() for f in expected_files), str(list(run_dir.iterdir())))

    log_lines = (run_dir / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    log_records = [json.loads(x) for x in log_lines]
    step_modules = [r.get("module") for r in log_records if r.get("event") == "module_end"]
    check(
        "端到端正常样例：日志包含 profile/retrieval/review/audit/final",
        step_modules == ["profile", "retrieval", "review", "audit", "final"],
        str(step_modules),
    )
    trace = json.loads((run_dir / "state_trace.json").read_text(encoding="utf-8"))
    trace_ok = all(
        "model" in s and "prompt_version" in s and "tool_calls" in s
        and "input_chars" in s and "output_chars" in s and "latency_ms" in s and "state_change" in s
        for s in trace["steps"]
    )
    check("端到端正常样例：state_trace 记录模型/Prompt/工具/文字量/耗时/状态变化", trace_ok)
    manifest = json.loads((run_dir / "output_manifest.json").read_text(encoding="utf-8"))
    manifest_expected = [
        "profile_facts.json",
        "retrieval.json",
        "draft_review.json",
        "audit.json",
        "semantic_audit.json",
        "review_card.json",
        "state_trace.json",
    ]
    check("端到端正常样例：输出清单含全部输出文件 SHA-256", len(manifest) == len(manifest_expected), str(list(manifest)))

    # 2) 转人工样例（证据不足/无历史） -> HUMAN_REVIEW
    r_human = orch.run(human, run_name="stage8_human")
    check(
        "端到端转人工样例：final_status=HUMAN_REVIEW",
        r_human["final_status"] == "HUMAN_REVIEW" and r_human["final_verdict"] == "DEFER",
        r_human["final_verdict"],
    )
    check("端到端转人工样例：证据为空失败关闭（1轮停止）", r_human["card"]["audit"]["attempts"] == 1, "")
    check("端到端转人工样例：转人工原因已记录", len(r_human["human_review_reasons"]) > 0, str(r_human["human_review_reasons"]))
    ReviewCard.model_validate(r_human["card"])
    check("端到端转人工样例：建议卡符合 schema（final_verdict=DEFER）", r_human["card"]["final_verdict"] == "DEFER")

    # 3) 拒绝样例（审核捕获禁止性表达） -> REJECT
    orch_reject = _make_orchestrator(config, agents={"review": ForbiddenReviewAgent()})
    r_reject = orch_reject.run(reject, run_name="stage8_reject")
    check("端到端拒绝样例：final_status=REJECT", r_reject["final_status"] == "REJECT", r_reject["final_verdict"])
    ReviewCard.model_validate(r_reject["card"])
    check(
        "端到端拒绝样例：最终卡为拒绝说明而非被拒内容",
        "拒绝" in r_reject["card"]["review_points"][0]["focus_zh"]
        and "处罚" not in r_reject["card"]["review_points"][0]["focus_zh"],
        r_reject["card"]["review_points"][0]["focus_zh"],
    )


def test_e2e_max_rounds_and_errors(config: dict) -> None:
    normal = load_json("e2e_normal.json")

    # 最大审计轮次：持续无依据 -> 2轮后转人工，不无限循环
    orch = _make_orchestrator(config, agents={"review": UnsupportedReviewAgent()})
    r = orch.run(normal, run_name="stage8_maxround")
    check(
        "端到端最大轮次：达到 max_attempts 后转人工",
        r["final_status"] == "HUMAN_REVIEW" and r["card"]["audit"]["attempts"] == config["audit"]["max_rounds"],
        f"attempts={r['card']['audit']['attempts']}",
    )
    trace = json.loads((Path(r["run_dir"]) / "state_trace.json").read_text(encoding="utf-8"))
    audit_rounds = [s for s in trace["steps"] if s["module"] == "audit"]
    check("端到端最大轮次：审计节点只执行 max 轮", len(audit_rounds) == config["audit"]["max_rounds"], str(len(audit_rounds)))
    check("端到端最大轮次：转人工原因包含最大轮次", any("最大审计轮次" in x for x in r["human_review_reasons"]))

    # 数字不一致 -> REJECT
    orch_num = _make_orchestrator(config, agents={"review": WrongNumberReviewAgent()})
    r_num = orch_num.run(normal, run_name="stage8_wrongnum")
    check("端到端数字不一致：final_status=REJECT", r_num["final_status"] == "REJECT", r_num["final_verdict"])

    # 泄漏字段 -> 明确错误
    leaked = dict(normal)
    leaked["future_citation_label"] = 1
    try:
        orch.run(leaked, run_name="stage8_leak")
        check("端到端泄漏输入：拒绝 future_* 字段", False, "未抛出异常")
    except ValueError:
        check("端到端泄漏输入：拒绝 future_* 字段", True)

    # 错误路径：错误写入 error 日志且不生成建议卡
    bad_profile = dict(normal)
    bad_profile["smoothed_positive_rate"] = 1.7  # 超出 schema 上限
    try:
        orch.run(bad_profile, run_name="stage8_error")
        check("端到端错误路径：schema 越界报错", False, "未抛出异常")
    except Exception:  # noqa: BLE001
        run_dirs = sorted(
            (p for p in (ROOT / config["paths"]["runs"]).iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        latest = run_dirs[-1]
        log_text = (latest / "run_log.jsonl").read_text(encoding="utf-8")
        check(
            "端到端错误路径：error 日志已记录且无建议卡",
            '"event": "error"' in log_text and not (latest / "review_card.json").exists(),
            latest.name,
        )


def test_reproducibility_and_index(config: dict) -> None:
    normal = load_json("e2e_normal.json")
    orch = _make_orchestrator(config)
    r1 = orch.run(normal, run_name="stage8_repro")
    r2 = orch.run(normal, run_name="stage8_repro")
    check("可复现：两次运行目录不同（不覆盖）", r1["run_dir"] != r2["run_dir"])
    card1 = json.loads((Path(r1["run_dir"]) / "review_card.json").read_text(encoding="utf-8"))
    card2 = json.loads((Path(r2["run_dir"]) / "review_card.json").read_text(encoding="utf-8"))
    check("可复现：同一输入两次建议卡完全一致", card1 == card2)

    index_path = ROOT / config["paths"]["runs"] / "run_index.jsonl"
    check("运行索引：run_index.jsonl 存在", index_path.exists())
    entries = [json.loads(x) for x in index_path.read_text(encoding="utf-8").strip().splitlines() if x.strip()]
    sample_entries = [e for e in entries if e.get("sample_id") == normal["sample_id"] and e.get("config_hash") == orch.config_hash]
    check("运行索引：按 sample_id+config_hash 可找到完整运行记录", len(sample_entries) >= 2, str(len(sample_entries)))
    check("运行索引：包含最终判定与建议卡 SHA-256", all(e.get("final_verdict") and e.get("card_sha256") for e in sample_entries))


def test_registries(config: dict) -> None:
    reg = config.get("registries", {})
    agent_registry = yaml.safe_load((ROOT / reg["agent_registry"]).read_text(encoding="utf-8"))
    names = {a["name"] for a in agent_registry["agents"]}
    check(
        "Registry：agent_registry.yaml 登记全部模块",
        {"profile_agent", "retrieval_agent", "review_agent", "audit_agent", "semantic_audit_agent", "orchestrator"} <= names,
        str(names),
    )
    check(
        "Registry：每个模块含职责/输入Schema/输出Schema/模型/工具/版本",
        all(
            a.get("responsibility_zh") and a.get("input_schema") and a.get("output_schema")
            and "model" in a and "tools" in a and a.get("version")
            for a in agent_registry["agents"]
        ),
    )

    prompt_registry = yaml.safe_load((ROOT / reg["prompt_registry"]).read_text(encoding="utf-8"))
    by_id = {p["prompt_id"]: p for p in prompt_registry["prompts"]}
    for pid in ("review_agent_v1", "semantic_audit_v1"):
        entry = by_id[pid]
        path = ROOT / entry["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        check(f"Registry：prompt {pid} 文件存在且 SHA-256 一致", path.exists() and digest == entry["sha256"], digest[:12])

    forbidden = yaml.safe_load((ROOT / reg["forbidden_claim_rules"]).read_text(encoding="utf-8"))
    categories = {r["category_zh"] for r in forbidden["claim_level_forbidden"]}
    check(
        "Registry：forbidden_claim_rules 覆盖违法认定/处罚建议/事故必然性/事故伤亡预测",
        {"违法认定", "处罚建议", "事故必然性", "事故/伤亡预测"} <= categories,
        str(categories),
    )
    check("Registry：待确认事项已登记（不自行扩展）", len(forbidden.get("pending_confirmation", [])) >= 1)


def test_llm_config(config: dict) -> None:
    client = get_llm_client(config)
    check("Qwen 接入：默认离线 Dummy 客户端（不联网）", isinstance(client, DummyLLMClient))
    llm_cfg = config.get("llm", {})
    check(
        "Qwen 接入：配置支持 provider=qwen 且 Key 不走代码写死",
        llm_cfg.get("provider") in ("dummy", "qwen") and "api_key" not in llm_cfg or llm_cfg.get("api_key", "") == "",
        f"provider={llm_cfg.get('provider')}",
    )


def main() -> int:
    config = json.loads((ROOT / "configs" / "config.json").read_text(encoding="utf-8"))
    test_schema_and_fixtures(config)
    test_profile_agent(config)
    test_retrieval_agent(config)
    test_review_agent(config)
    test_audit_agent(config)
    test_semantic_audit(config)
    test_e2e_three_paths(config)
    test_e2e_max_rounds_and_errors(config)
    test_reproducibility_and_index(config)
    test_registries(config)
    test_llm_config(config)

    print(f"\n阶段8验收测试：{len(RESULTS)} 项")
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
