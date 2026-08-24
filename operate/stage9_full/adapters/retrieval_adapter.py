"""学生2知识库 -> evidence 检索适配器（阶段9正式版）。"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

from adapters import paths  # noqa: F401
from adapters import schema_adapter
from src.common.pydantic_schemas import EvidenceItem, RetrievalResult  # noqa: E402

SOURCE_TYPE_MAP = {
    "法规正文": "regulation",
    "解释资料": "interpretation",
    "历史资料": "archive",
    "数据定义": "data_definition",
    "现场工作手册": "field_manual",
}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_read_text(path))))


class Stage9RetrievalAdapter:
    def __init__(self, data: dict[str, Path], top_k: int = 3, use_rag: bool = True):
        self.knowledge_dir = data["knowledge_dir"]
        self.top_k = top_k
        self.chunks = self._load_chunks()
        self.mapping = self._load_mapping()
        self.inventory = self._load_inventory()
        self.rag = self._try_load_rag(data.get("rag_retriever_path")) if use_rag else None

    def _load_chunks(self) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in _read_text(self.knowledge_dir / "chunks" / "regulation_chunks.jsonl").splitlines()
            if line.strip()
        ]

    def _load_mapping(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in _read_csv(self.knowledge_dir / "standard_document_mapping.csv"):
            cid = (row.get("片段编号") or "").strip()
            out[cid] = {
                "standard_number": (row.get("OSHA标准编号") or "").strip(),
                "section": (row.get("条款编号") or "").strip(),
                "source_type": SOURCE_TYPE_MAP.get(row.get("来源类型", ""), "regulation"),
                "source_url": (row.get("来源网址") or "").strip(),
                "retrieved_at": (row.get("获取日期") or "").strip(),
            }
        return out

    def _load_inventory(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in _read_csv(self.knowledge_dir / "document_inventory.csv"):
            doc_id = (row.get("文档编号") or "").strip()
            doc = {
                "document_id": doc_id,
                "title": (row.get("标题") or "").strip(),
                "source_type": SOURCE_TYPE_MAP.get(row.get("来源类型", ""), "regulation"),
                "source_url": (row.get("来源网址") or "").strip(),
                "effective_date": (row.get("生效日期") or "").strip() or None,
                "retrieved_at": (row.get("获取日期") or "").strip(),
                "is_archived": str(row.get("是否为历史资料", "")).strip() in ("是", "TRUE", "1"),
            }
            keys = {doc_id}
            for part in str(row.get("OSHA标准编号") or "").split(","):
                p = part.strip()
                if p:
                    keys.add(p)
            for k in keys:
                out.setdefault(k, doc)
        return out

    def _try_load_rag(self, rag_path: Path | None):
        if not rag_path or not Path(rag_path).exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location("rag_retriever", rag_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["rag_retriever"] = module
            spec.loader.exec_module(module)
            return module.RAGRetriever()
        except Exception:
            return None

    def _evidence(self, raw: dict[str, Any], rank: int) -> EvidenceItem | None:
        chunk_id = raw.get("chunk_id", "")
        meta = self.mapping.get(chunk_id, {})
        standard_number = meta.get("standard_number") or raw.get("standard", "")
        section = meta.get("section") or raw.get("section", "")
        if not standard_number and not section:
            return None
        doc = self.inventory.get(standard_number) or {}
        document_id = doc.get("document_id", "UNKNOWN")
        return EvidenceItem(
            evidence_id=f"regulation:{document_id}#{section}",
            document_id=document_id,
            standard_number=standard_number,
            section=section,
            title=doc.get("title") or raw.get("text", "")[:120],
            text=raw.get("text", ""),
            source_type=meta.get("source_type") or doc.get("source_type", "regulation"),
            source_url=meta.get("source_url") or doc.get("source_url", ""),
            effective_date=doc.get("effective_date"),
            retrieved_at=meta.get("retrieved_at") or doc.get("retrieved_at", ""),
            is_archived=bool(doc.get("is_archived", False)),
            score=float(raw.get("score", 0.0)),
            rank=rank,
        )

    def _keyword_fallback(self, standards: list[str], query_text: str) -> list[dict[str, Any]]:
        q_tokens = set(re.findall(r"[a-z0-9]+", str(query_text).lower()))
        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk in self.chunks:
            chunk_std = str(chunk.get("standard", ""))
            if standards:
                hit = any(
                    chunk_std == s or chunk_std.startswith(s + ".") or s.startswith(chunk_std + ".")
                    for s in standards
                )
                if not hit:
                    continue
            text_tokens = set(re.findall(r"[a-z0-9]+", f"{chunk.get('text','')} {chunk.get('section','')}".lower()))
            score = float(len(q_tokens & text_tokens)) if q_tokens else 1.0
            if any(chunk_std.startswith(s) for s in standards):
                score += 10.0
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
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
            std = schema_adapter.convert_standard(raw)
            if std:
                standards.append(std)

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
                {"chunk_id": c.get("chunk_id", ""), "standard": c.get("standard", ""),
                 "section": c.get("section", ""), "text": c.get("text", ""), "score": 0.0}
                for c in self._keyword_fallback(standards, query)
            ]

        for rank, raw in enumerate(raw_items, start=1):
            item = self._evidence(raw, rank)
            if item is not None:
                result.items.append(item)
        if not result.items:
            result.empty_reason = "知识库未覆盖该标准编号或查询（禁止编造条款）"
        return result
