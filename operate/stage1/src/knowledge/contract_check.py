"""知识库契约核对：统一后的片段是否符合 evidence_schema.json 的字段要求。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.common.pydantic_schemas import EvidenceItem, RetrievalResult
from src.agents.retrieval_agent import RetrievalAgent


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    chunks_path = root / "knowledge" / "chunks" / "regulation_chunks.jsonl"
    mapping_path = root / "knowledge" / "chunks" / "standard_document_mapping.csv"

    chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    required = ["evidence_id", "document_id", "standard_number", "section", "text", "source_type", "source_url", "retrieved_at", "is_archived"]
    missing_rows: list[dict] = []
    valid_sources = {"regulation", "interpretation", "data_definition", "field_manual", "archive"}
    for c in chunks:
        miss = [f for f in required if f not in c]
        bad_type = c.get("source_type") not in valid_sources
        if miss or bad_type:
            missing_rows.append({"chunk_id": c.get("chunk_id"), "missing": miss, "bad_source_type": bad_type})

    retrieval = RetrievalAgent(chunks_path=chunks_path, mapping_path=mapping_path, top_k=3, min_score=1.0)
    hit = retrieval.run(["1910.269"], ["R1"], query_id="contract-check-hit")
    miss = retrieval.run(["1910.999"], [], query_id="contract-check-miss")
    hit_ok = isinstance(hit, RetrievalResult) and len(hit.items) > 0 and all(isinstance(i, EvidenceItem) for i in hit.items)

    report = {
        "n_chunks": len(chunks),
        "n_missing_required": len(missing_rows),
        "missing_samples": missing_rows[:20],
        "retrieval_hit_1910_269": hit_ok,
        "retrieval_hit_n": len(hit.items),
        "retrieval_miss_empty": len(miss.items) == 0 and bool(miss.empty_reason),
    }
    out = root / "docs" / "knowledge_contract_check_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"知识库契约核对：片段 {report['n_chunks']}，缺字段 {report['n_missing_required']}，命中 1910.269={hit_ok}，未知标准返回空={report['retrieval_miss_empty']}")
    print(f"报告：{out}")
    return 1 if (missing_rows or not hit_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
