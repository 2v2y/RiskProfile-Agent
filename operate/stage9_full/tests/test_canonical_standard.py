"""Canonical Standard 回归测试（离线、确定性，不依赖 HuggingFace/FAISS/BGE/外网）。

职责（见 docs/standard_consistency_analysis.md）：
1. 标准编号 canonicalization：DOL 原值 / 映射键 / 官方格式 -> 统一 Canonical Standard；
2. 多标准输入 canonicalize；
3. RAG path coverage discipline：有覆盖才检索、无覆盖不前缀误命中、1926.65 != 1926.651；
4. adapter 与 canonical_standard 的接口，以及 1926.651 <-> 1926.0651 等价回归。

所有用例都用 use_rag=False 或 fake RAG：**不初始化真实 RAG**，
因此不会触发嵌入模型联网下载或 FAISS 加载。
真实 RAG 的集成验证放到独立 integration 脚本（显式使用本地模型路径）。

可单独运行：python -m tests.test_canonical_standard
也可被 pytest 收集：共 4 个 test_* 函数。
"""

from __future__ import annotations

import sys
from typing import Any

from adapters import canonical_standard
from adapters import paths  # noqa: F401
from adapters.retrieval_adapter import Stage9RetrievalAdapter
from experiments import common


def _adapter(top_k: int = 3) -> Stage9RetrievalAdapter:
    """离线构造适配器：use_rag=False，绝不加载学生2 RAG/模型。"""
    config, data, _ = common.setup()
    return Stage9RetrievalAdapter(data, top_k=top_k, use_rag=False)


# --------------------------------------------------------------------------- 1. canonicalization
def test_canonicalize_rules() -> None:
    """1) 标准编号 canonicalization 规则（含 1926.651 <-> 1926.0651 等价）。"""
    cases = [
        # (raw, expected canonical, expected mapping_key)
        ("1926.651", "1926.651", "1926.0651"),
        ("1926.0651", "1926.651", "1926.0651"),
        ("19260651", "1926.651", "1926.0651"),
        ("19260651 A", "1926.651", "1926.0651"),
        ("19260651 B", "1926.651", "1926.0651"),
        ("19260651 C01 I", "1926.651", "1926.0651"),
        ("1910.132", "1910.132", "1910.0132"),
        ("19100132", "1910.132", "1910.0132"),
        ("19100132 Q01", "1910.132", "1910.0132"),
        ("1926.65", "1926.65", "1926.0065"),  # 不能与 1926.651 混淆
        ("1926.1050", "1926.1050", "1926.105"),
        ("16VAC25-60-130", "16VAC25-60-130", "16VAC25-60-130"),  # 非联邦原样
    ]
    for raw, expected, expected_key in cases:
        canon = canonical_standard.canonicalize(raw)
        assert canon == expected, f"{raw!r} -> {canon!r}, expected {expected!r}"
        assert canonical_standard.mapping_key(canon) == expected_key, (
            f"mapping_key({canon!r}) != {expected_key!r}"
        )

    # 1926.651 与知识库可能出现的 1926.0651 是同一标准（同一 Canonical）
    assert canonical_standard.canonicalize("1926.651") == canonical_standard.canonicalize("1926.0651")
    # 规则是「节号去前导零」（str(int(section))），不是字符串替换：
    # 1926.0651 -> 1926.651 正确；1926.65 -> 1926.65（保持自身，不吞并 1926.651）
    assert canonical_standard.canonicalize("1926.65") != "1926.651"
    assert canonical_standard.canonicalize("1926.65") != canonical_standard.canonicalize("1926.0651")

    # 2) 多标准输入
    raw_list = ["1926.651", "1910.132"]
    canon_list = [canonical_standard.canonicalize(x) for x in raw_list]
    assert canon_list == ["1926.651", "1910.132"]


# --------------------------------------------------------------------------- 2. 端到端代表用例
def test_canonical_cases() -> None:
    """2) 5 个代表性标准端到端：raw -> canonical -> R1-R9 mapping -> adapter 检索。"""
    config, data, _ = common.setup()
    adapter = _adapter(top_k=config["retrieval"]["top_k"])
    mapping = canonical_standard.R1R9Mapping(data["r1r9_mapping"])

    cases = [
        ("19260651 A", "1926.651", "1926.0651", "R8", "covered"),  # 6.0-frozen 已补齐 1926.651
        ("19261053", "1926.1053", "1926.1053", "R4", "covered"),
        ("19260502", "1926.502", "1926.0502", "R4", "covered"),
        ("19260960", "1926.960", "1926.096", "R1", "covered"),
        ("19100132", "1910.132", "1910.0132", "R2", "covered"),
    ]
    for raw, expected_canon, expected_key, expected_r, expected_status in cases:
        canon = canonical_standard.canonicalize(raw)
        assert canon == expected_canon, f"{raw!r} canonical={canon!r}"
        assert canonical_standard.mapping_key(canon) == expected_key
        lookup = mapping.lookup(canon)
        assert lookup["status"] == "FOUND", f"{raw}: {lookup['status']}"
        assert lookup["r_category"] == expected_r, f"{raw}: R={lookup['r_category']}"
        result = adapter.run([raw], query_id=f"case-{raw}")
        status = next(
            (s for s in result.standard_statuses if s.get("requested_standard") == raw), None
        )
        assert status is not None and status["retrieval_status"] == expected_status, (
            f"{raw}: {status}"
        )
        if expected_status == "coverage_gap":
            assert result.items == [], f"{raw}: 覆盖缺失不应返回证据"
            assert result.empty_reason and "覆盖" in result.empty_reason
        else:
            assert result.items, f"{raw}: 应有证据"
            assert all(i.standard_number == expected_canon for i in result.items)


# --------------------------------------------------------------------------- 3. RAG path coverage discipline
class _FakeRAG:
    """模拟学生2 RAG：标准查询走精确/前缀过滤，无匹配时纯 BGE 回退到无关片段。"""

    _UNRELATED = {
        "chunk_id": "chunk_99999",
        "standard": "1926.1436",
        "section": "1926.1436",
        "text": "unrelated crane text",
        "score": 0.2,
    }

    def __init__(self, chunks: list[dict[str, Any]], poison_query: str | None = None):
        self.chunks = [c for c in chunks if c.get("standard")]
        self.calls: list[str] = []
        # poison_query：对该查询混入一条无关片段，用于验证 verification_rejected
        self.poison_query = poison_query

    def search(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        self.calls.append(str(query).strip())
        results = [c for c in self.chunks if c.get("standard") == query]
        if self.poison_query == str(query).strip():
            results.insert(0, dict(self._UNRELATED))
        if results:
            return results[:k]
        prefix = [c for c in self.chunks if str(c.get("standard", "")).startswith(query)]
        if prefix:
            return prefix[:k]
        # 纯 BGE 回退：返回无关片段（模拟误命中）
        return [dict(self._UNRELATED)]


def test_rag_path_coverage_discipline() -> None:
    """3) RAG 覆盖纪律：有覆盖才检索；无覆盖不调 RAG；前缀不误命中；回退垃圾被丢弃。"""
    adapter = _adapter()
    fake = _FakeRAG(adapter.chunks)
    adapter.rag = fake

    # 3a) 无覆盖（非联邦州法规代码，知识库不含）：绝不调用 RAG
    gap = adapter.run(["16VAC25-60-130"], query_id="gap")
    assert gap.empty_reason and "覆盖" in gap.empty_reason
    assert fake.calls == [], f"coverage_gap 不应调用 RAG: {fake.calls}"

    # 3b) 1926.65 已覆盖，但检索只能返回 1926.65 自身片段，
    #     不能前缀误命中 1926.651 / 1926.0651（6.0 KB 同时含有这两个标准）
    near = adapter.run(["1926.65"], query_id="near")
    status = next(s for s in near.standard_statuses if s["requested_standard"] == "1926.65")
    assert status["retrieval_status"] == "covered", status
    assert fake.calls == ["1926.65"], f"1926.65 已覆盖，应按 Canonical 调用 RAG: {fake.calls}"
    assert near.items, "1926.65 应有证据"
    assert all(i.standard_number == "1926.65" for i in near.items), [
        i.standard_number for i in near.items
    ]
    assert not any(i.standard_number in {"1926.651", "1926.0651"} for i in near.items)
    fake.calls.clear()

    # 3c) 有覆盖的标准：按单个 Canonical 逐条调用 RAG，返回按请求标准过滤
    covered = adapter.run(["19100132", "19260502"], query_id="multi-covered")
    assert fake.calls == ["1910.132", "1926.502"], f"应按 Canonical 逐个查询: {fake.calls}"
    assert len(covered.items) <= 3
    assert all(i.standard_number in {"1910.132", "1926.502"} for i in covered.items)
    assert all(s["retrieval_status"] == "covered" for s in covered.standard_statuses)

    # 3d) 模拟 RAG 纯 BGE 回退：无关片段被丢弃（verification_rejected 记录）
    poisoned = _FakeRAG(adapter.chunks, poison_query="1926.502")
    adapter.rag = poisoned
    only = adapter.run(["19260502"], query_id="only")
    assert only.items and all(i.standard_number == "1926.502" for i in only.items)
    assert any(
        s["retrieval_status"] == "verification_rejected" for s in only.standard_statuses
    ), only.standard_statuses


# --------------------------------------------------------------------------- 4. 1926.651 <-> 1926.0651 回归
def test_kb_padded_standard_equivalence() -> None:
    """4) 回归：若知识库存在 1926.0651（映射键格式），请求侧 1926.651 必须命中；
    同时 1926.65 不得被识别为 1926.651。

    注：6.0-frozen 已含 1926.651 官方格式片段；本用例额外注入 1926.0651（映射键格式）
    片段，验证适配层对"知识库以 padded 形式存在"的等价逻辑（不改任何冻结文件）。
    """
    adapter = _adapter()

    # 注入一条 KB chunk：standard = 1926.0651（学生2映射键格式）
    fixture = {
        "chunk_id": "chunk_fixture_0651",
        "standard": "1926.0651",
        "section": "1926.651",
        "text": "1926.651 Excavations. Protection of employees in excavations.",
    }
    adapter.chunks.append(fixture)
    adapter._kb_standards.add(canonical_standard.canonicalize(fixture["standard"]))

    # 请求侧 1926.651 -> 命中 KB 的 1926.0651（同一 Canonical）
    covered, mode = adapter._kb_coverage("1926.651")
    assert covered is True and mode == "exact_canonical", (covered, mode)
    result = adapter.run(["1926.651"], query_id="equiv")
    status = next(s for s in result.standard_statuses if s["requested_standard"] == "1926.651")
    assert status["retrieval_status"] == "covered", status
    assert result.items, "1926.651 应命中 1926.0651 片段"
    assert all(i.standard_number == "1926.651" for i in result.items), [
        i.standard_number for i in result.items
    ]

    # 反向等价：请求 1926.0651 同样命中
    reverse = adapter.run(["1926.0651"], query_id="equiv-rev")
    assert reverse.items and all(i.standard_number == "1926.651" for i in reverse.items)

    # 1926.65 在 6.0 KB 中已覆盖，但检索只能返回 1926.65 自身，不能匹配 1926.651 / 1926.0651
    assert adapter._kb_coverage("1926.65")[0] is True
    near = adapter.run(["1926.65"], query_id="near2")
    assert near.items and all(i.standard_number == "1926.65" for i in near.items)
    assert not any(i.standard_number in {"1926.651", "1926.0651"} for i in near.items)


# --------------------------------------------------------------------------- 直接运行入口
def main() -> int:
    test_canonicalize_rules()
    test_canonical_cases()
    test_rag_path_coverage_discipline()
    test_kb_padded_standard_equivalence()
    print("test_canonical_standard PASS (4 tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
