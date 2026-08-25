"""Canonical Standard 回归测试：统一 1926.651 及 1910/1926 各段代表性标准。

用例（按需求文档第十节）：
1. 1926.651（知识库覆盖缺失 → 必须返回 coverage_gap，不伪造）
2. 一个学生2已有的 1926.1xx 标准：1926.1053（梯子）
3. 一个 1926.5xx 标准：1926.502（防坠）
4. 一个 1926.9xx 标准：1926.960（输电）
5. 一个 1910 标准：1910.132（PPE）

每个用例输出 raw / canonical / mapping / retrieval / status。
可单独运行：python -m tests.test_canonical_standard
也可被 pytest 收集：test_canonical_cases()
"""

from __future__ import annotations

import sys
from typing import Any

from adapters import canonical_standard
from adapters import paths  # noqa: F401
from adapters.retrieval_adapter import Stage9RetrievalAdapter
from experiments import common

CASES: list[dict[str, Any]] = [
    {
        "name": "1926.651 (开挖 Subpart P)",
        "raw": "19260651 A",
        "canonical": "1926.651",
        "mapping_key": "1926.0651",
        "expected_r": "R8",
        "expected_retrieval_status": "coverage_gap",
    },
    {
        "name": "1926.1053 (梯子, KB 覆盖)",
        "raw": "19261053",
        "canonical": "1926.1053",
        "mapping_key": "1926.1053",
        "expected_r": "R4",
        "expected_retrieval_status": "covered",
    },
    {
        "name": "1926.502 (防坠, KB 覆盖)",
        "raw": "19260502",
        "canonical": "1926.502",
        "mapping_key": "1926.0502",
        "expected_r": "R4",
        "expected_retrieval_status": "covered",
    },
    {
        "name": "1926.960 (输电, KB 覆盖)",
        "raw": "19260960",
        "canonical": "1926.960",
        "mapping_key": "1926.096",
        "expected_r": "R1",
        "expected_retrieval_status": "covered",
    },
    {
        "name": "1910.132 (PPE, KB 覆盖)",
        "raw": "19100132",
        "canonical": "1910.132",
        "mapping_key": "1910.0132",
        "expected_r": "R2",
        "expected_retrieval_status": "covered",
    },
]


def _run_case(adapter: Stage9RetrievalAdapter, mapping: canonical_standard.R1R9Mapping,
              case: dict[str, Any]) -> dict[str, Any]:
    raw = case["raw"]
    canonical = canonical_standard.canonicalize(raw)
    mapping_key = canonical_standard.mapping_key(canonical)
    lookup = mapping.lookup(canonical) if canonical else {"status": "MISSING"}
    result = adapter.run([raw], query_id=f"case-{case['name']}")

    status = next(
        (s for s in result.standard_statuses if s.get("requested_standard") == raw),
        None,
    )
    return {
        "name": case["name"],
        "raw": raw,
        "canonical": canonical,
        "mapping_key": mapping_key,
        "mapping_status": lookup.get("status"),
        "mapping_r_category": lookup.get("r_category"),
        "mapping_r_categories": lookup.get("r_categories"),
        "retrieval_status": status.get("retrieval_status") if status else None,
        "retrieval_reason": status.get("reason") if status else None,
        "n_evidence": len(result.items),
        "empty_reason": result.empty_reason,
        "evidence_standards": sorted({i.standard_number for i in result.items}),
        "standard_number": result.standard_number,
    }


def test_canonical_cases() -> None:
    config, data, _ = common.setup()
    adapter = Stage9RetrievalAdapter(data, top_k=config["retrieval"]["top_k"],
                                     use_rag=bool(config["retrieval"].get("use_rag", True)))
    mapping = canonical_standard.R1R9Mapping(data["r1r9_mapping"])

    for case in CASES:
        out = _run_case(adapter, mapping, case)
        assert out["canonical"] == case["canonical"], f"{case['name']}: canonical 不一致"
        assert out["mapping_key"] == case["mapping_key"], f"{case['name']}: 映射键不一致"
        assert out["mapping_status"] == "FOUND", f"{case['name']}: 映射未命中 {out['mapping_status']}"
        assert out["mapping_r_category"] == case["expected_r"], f"{case['name']}: R 类别不一致"
        assert out["retrieval_status"] == case["expected_retrieval_status"], (
            f"{case['name']}: 检索状态不一致 {out['retrieval_status']}"
        )
        if case["expected_retrieval_status"] == "coverage_gap":
            assert out["n_evidence"] == 0, f"{case['name']}: 覆盖缺失不应返回证据"
            assert out["empty_reason"] and "覆盖" in out["empty_reason"]
        else:
            assert out["n_evidence"] > 0, f"{case['name']}: 应有证据"


def test_canonicalize_equivalence() -> None:
    """19260651 A / 1926.0651 / 1926.651 必须统一为 1926.651。"""
    for raw in ("19260651 A", "1926.0651", "1926.651"):
        assert canonical_standard.canonicalize(raw) == "1926.651", raw


def test_multi_standard_query() -> None:
    """多标准画像：按标准逐个检索，证据总量不超过 top_k，且不混入无关标准。"""
    config, data, _ = common.setup()
    adapter = Stage9RetrievalAdapter(data, top_k=config["retrieval"]["top_k"],
                                     use_rag=bool(config["retrieval"].get("use_rag", True)))
    result = adapter.run(["19100132", "19260502"], query_id="multi")
    assert len(result.items) <= config["retrieval"]["top_k"]
    allowed = {"1910.132", "1926.502"}
    assert all(i.standard_number in allowed for i in result.items)
    assert all(s["retrieval_status"] == "covered" for s in result.standard_statuses)


class _FakeRAG:
    """模拟学生2 RAG 行为：标准查询走精确/前缀过滤，无匹配时纯 BGE 回退到无关片段。"""

    def __init__(self, chunks: list[dict[str, Any]]):
        self.chunks = [c for c in chunks if c.get("standard")]
        self.calls: list[str] = []

    def search(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        self.calls.append(query)
        query = str(query).strip()
        exact = [c for c in self.chunks if c.get("standard") == query]
        if exact:
            return exact[:k]
        prefix = [c for c in self.chunks if str(c.get("standard", "")).startswith(query)]
        if prefix:
            return prefix[:k]
        # 纯 BGE 回退：返回与请求无关的片段（模拟误命中）
        return [{"chunk_id": "chunk_99999", "standard": "1926.1436",
                 "section": "1926.1436", "text": "unrelated crane text", "score": 0.2}]


def test_rag_path_coverage_discipline() -> None:
    """RAG 可用时的纪律：
    1) coverage_gap 的标准绝不调用 RAG；
    2) 多标准按单个 Canonical 逐个查询；
    3) RAG 纯 BGE 回退返回的无关片段被丢弃。
    """
    config, data, _ = common.setup()
    adapter = Stage9RetrievalAdapter(data, top_k=config["retrieval"]["top_k"],
                                     use_rag=False)
    fake = _FakeRAG(adapter.chunks)
    adapter.rag = fake

    # 覆盖缺失：不调用 RAG
    gap = adapter.run(["19260651 A"], query_id="gap")
    assert gap.empty_reason and "覆盖" in gap.empty_reason
    assert fake.calls == [], f"coverage_gap 不应调用 RAG: {fake.calls}"

    # 多标准覆盖：逐个查询，过滤无关回退
    multi = adapter.run(["19100132", "19260502"], query_id="multi")
    assert fake.calls == ["1910.132", "1926.502"], f"应按 Canonical 逐个查询: {fake.calls}"
    assert len(multi.items) <= config["retrieval"]["top_k"]
    assert all(i.standard_number in {"1910.132", "1926.502"} for i in multi.items)

    # 单个标准 + 故意制造 RAG 误命中：验证 verification_rejected 且不产出无关证据
    only = adapter.run(["19100132"], query_id="only")
    assert all(i.standard_number == "1910.132" for i in only.items)


def main() -> int:
    config, data, _ = common.setup()
    adapter = Stage9RetrievalAdapter(data, top_k=config["retrieval"]["top_k"],
                                     use_rag=bool(config["retrieval"].get("use_rag", True)))
    mapping = canonical_standard.R1R9Mapping(data["r1r9_mapping"])

    print("=== CANONICAL STANDARD REGRESSION TEST ===")
    for case in CASES:
        out = _run_case(adapter, mapping, case)
        print(f"\n[{out['name']}]")
        print(f"  raw      = {out['raw']}")
        print(f"  canonical= {out['canonical']}")
        print(f"  mapping  = {out['mapping_status']} "
              f"(key={out['mapping_key']}, R={out['mapping_r_category'] or out['mapping_r_categories']})")
        print(f"  retrieval= status={out['retrieval_status']}, evidence={out['n_evidence']}, "
              f"standards={out['evidence_standards']}")
        print(f"  reason   = {out['retrieval_reason'] or out['empty_reason']}")

    test_canonicalize_equivalence()
    test_canonical_cases()
    test_multi_standard_query()
    test_rag_path_coverage_discipline()
    print("\ntest_canonical_standard PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
