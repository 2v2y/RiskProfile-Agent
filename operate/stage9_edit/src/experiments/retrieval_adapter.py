"""阶段9检索适配器：包装学生2的 FAISS/BGE 混合检索器，并保留离线关键词回退。

目标：
1. 替换/包装阶段1的 TF-IDF 检索，使用学生2冻结的 vector_db 和 rag_retriever；
2. 服务器缺少 faiss/sentence-transformers/torch 时，回退到确定性关键词检索，
   保证阶段9干跑仍可完成；
3. 输出仍采用 evidence_schema.json 的 RetrievalResult 结构，便于下游统一调用。
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from src.experiments.paths import STAGE9_ROOT

from src.common.pydantic_schemas import EvidenceItem, RetrievalResult  # noqa: E402


_SOURCE_TYPE_MAP = {
    "法规正文": "regulation",
    "解释资料": "interpretation",
    "历史资料": "archive",
    "数据定义": "data_definition",
    "现场工作手册": "field_manual",
}


def convert_standard(raw: str) -> str | None:
    """DOL 格式 -> 知识库格式：'19100132 Q01' -> '1910.132'。"""
    raw = str(raw or "").strip()
    parts = raw.split()
    if not parts:
        return None
    code = parts[0]
    if not code.isdigit() or len(code) < 7:
        return None
    part = code[:4]
    if part not in ("1910", "1926"):
        return None
    section = str(int(code[4:]))
    return f"{part}.{section}"


class Stage9RetrievalAdapter:
    def __init__(
        self,
        root: Path | str | None = None,
        top_k: int = 3,
        use_rag: bool = True,
    ):
        self.root = Path(root) if root else STAGE9_ROOT
        self.top_k = top_k
        self.use_rag = use_rag
        self.chunks = self._load_chunks()
        self.inventory = self._load_inventory()
        self.mapping = self._load_mapping()
        self.rag = self._try_load_rag() if use_rag else None

    # ---------------------------------------------------------------- 加载
    def _load_chunks(self) -> list[dict[str, Any]]:
        path = self.root / "knowledge" / "chunks" / "regulation_chunks.jsonl"
        chunks: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(json.loads(line))
        return chunks

    def _load_inventory(self) -> dict[str, dict[str, Any]]:
        path = self.root / "knowledge" / "document_inventory.csv"
        inventory: dict[str, dict[str, Any]] = {}
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                std = (row.get("OSHA标准编号") or "").strip()
                inventory.setdefault(
                    std,
                    {
                        "document_id": (row.get("文档编号") or "").strip(),
                        "title": (row.get("标题") or "").strip(),
                        "source_type": _SOURCE_TYPE_MAP.get(row.get("来源类型", ""), "regulation"),
                        "source_url": (row.get("来源网址") or "").strip(),
                        "effective_date": (row.get("生效日期") or "").strip() or None,
                        "retrieved_at": (row.get("获取日期") or "").strip(),
                        "is_archived": str(row.get("是否为历史资料", "")).strip() in ("是", "TRUE", "1"),
                    },
                )
        return inventory

    def _load_mapping(self) -> dict[str, dict[str, Any]]:
        path = self.root / "knowledge" / "chunks" / "standard_document_mapping.csv"
        mapping: dict[str, dict[str, Any]] = {}
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                chunk_id = (row.get("片段编号") or "").strip()
                mapping[chunk_id] = {
                    "standard_number": (row.get("OSHA标准编号") or "").strip(),
                    "section": (row.get("条款编号") or "").strip(),
                    "source_type": _SOURCE_TYPE_MAP.get(row.get("来源类型", ""), "regulation"),
                    "source_url": (row.get("来源网址") or "").strip(),
                    "retrieved_at": (row.get("获取日期") or "").strip(),
                }
        return mapping

    def _try_load_rag(self) -> Any | None:
        try:
            sys.path.insert(0, str(self.root / "src" / "retrieval"))
            from rag_retriever import RAGRetriever

            return RAGRetriever()
        except Exception:
            return None

    # ---------------------------------------------------------------- 证据转换
    def _evidence_from_result(self, result: dict[str, Any], rank: int) -> EvidenceItem | None:
        chunk_id = result.get("chunk_id", "")
        meta = self.mapping.get(chunk_id, {})
        standard_number = meta.get("standard_number") or result.get("standard", "")
        section = meta.get("section") or result.get("section", "")
        if not standard_number and not section:
            return None
        inv = self.inventory.get(standard_number, {})
        document_id = inv.get("document_id", "UNKNOWN")
        return EvidenceItem(
            evidence_id=f"regulation:{document_id}#{section}",
            document_id=document_id,
            standard_number=standard_number,
            section=section,
            title=inv.get("title") or result.get("text", "")[:120],
            text=result.get("text", ""),
            source_type=meta.get("source_type") or inv.get("source_type", "regulation"),
            source_url=meta.get("source_url") or inv.get("source_url", ""),
            effective_date=inv.get("effective_date"),
            retrieved_at=meta.get("retrieved_at") or inv.get("retrieved_at", ""),
            is_archived=bool(inv.get("is_archived", False)),
            score=float(result.get("score", 0.0)),
            rank=rank,
        )

    # ---------------------------------------------------------------- 检索
    def _fallback_keyword(
        self,
        standards: list[str],
        query_text: str,
    ) -> list[dict[str, Any]]:
        q_tokens = set(re.findall(r"[a-z0-9]+", str(query_text).lower()))
        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk in self.chunks:
            chunk_std = str(chunk.get("standard", ""))
            if standards:
                matched = any(
                    chunk_std == s or chunk_std.startswith(s + ".") or s.startswith(chunk_std + ".")
                    for s in standards
                )
                if not matched:
                    continue
            text_tokens = set(
                re.findall(r"[a-z0-9]+", f"{chunk.get('text', '')} {chunk.get('section', '')}".lower())
            )
            score = float(len(q_tokens & text_tokens)) if q_tokens else 1.0
            if any(chunk_std.startswith(s) for s in standards):
                score += 10.0
            scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [c for _, c in scored[: self.top_k]]

    def run(
        self,
        standard_codes: list[str],
        risk_categories: list[str] | None = None,
        query_id: str = "q0",
        profile_facts: list[dict[str, Any]] | None = None,
        query_text: str | None = None,
    ) -> RetrievalResult:
        risk_categories = list(risk_categories or [])
        standards: list[str] = []
        for raw in standard_codes or []:
            raw = str(raw).strip()
            if re.match(r"^(1910|1926)\.\d+", raw):
                standards.append(raw)
                continue
            conv = convert_standard(raw)
            if conv:
                standards.append(conv)

        result = RetrievalResult(
            query_id=query_id,
            standard_number=",".join(standards) if standards else "UNKNOWN",
            risk_categories=risk_categories,
            items=[],
            empty_reason=None,
        )

        query = query_text or " ".join(standards)
        if not standards and not query_text:
            result.empty_reason = "画像中没有历史OSHA标准编号，无法构造检索问题"
            return result

        raw_items: list[dict[str, Any]] = []
        if self.rag is not None:
            try:
                raw_items = self.rag.search(query, k=self.top_k)
            except Exception:
                raw_items = []

        if not raw_items:
            raw_items = [
                {
                    "chunk_id": c.get("chunk_id", ""),
                    "standard": c.get("standard", ""),
                    "section": c.get("section", ""),
                    "text": c.get("text", ""),
                    "score": 0.0,
                }
                for c in self._fallback_keyword(standards, query)
            ]

        for rank, raw in enumerate(raw_items, start=1):
            item = self._evidence_from_result(raw, rank)
            if item is not None:
                result.items.append(item)

        if not result.items:
            result.empty_reason = "知识库未覆盖该标准编号或查询（禁止编造条款）"
        return result
