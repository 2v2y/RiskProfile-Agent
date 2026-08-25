"""学生2知识库 -> evidence 检索适配器（阶段9正式版，含 Canonical Standard 统一层）。

标准编号链路：
    raw standard（DOL 原值 / 映射键 / 官方格式）
        ↓ canonical_standard.canonicalize()
    Canonical Standard（官方引用格式，如 1926.651）
        ↓ 知识库覆盖预检 + 调用学生2已验证 RAG（不修改学生2算法/索引/模型）
    RetrievalResult（含逐标准 standard_statuses 审计字段）

原则（docs/standard_consistency_analysis.md）：
1. 所有进入检索的标准必须先 canonicalize；
2. 知识库无该标准正文时，不调用学生2 RAG 的纯 BGE 语义回退（避免 fallback 误命中），
   返回明确 coverage gap，禁止伪造；
3. 不修改学生2 rag_retriever.py、embedding、FAISS index、chunking、top-k、hybrid 策略。
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

from adapters import canonical_standard
from adapters import paths  # noqa: F401
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
        self._kb_standards = self._build_kb_standards()
        self.rag = self._try_load_rag(data.get("rag_retriever_path")) if use_rag else None

    # ---------------------------------------------------------------- 数据加载
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

    def _build_kb_standards(self) -> set[str]:
        """知识库实际覆盖的标准集合（chunk standard / mapping / inventory，统一官方格式）。"""
        out: set[str] = set()
        for c in self.chunks:
            s = str(c.get("standard", "")).strip()
            if s:
                out.add(s)
        for meta in self.mapping.values():
            s = str(meta.get("standard_number") or "").strip()
            if s:
                out.add(s)
        for key in self.inventory:
            if key:
                out.add(key)
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

    # ---------------------------------------------------------------- 证据构造
    def _evidence(self, raw: dict[str, Any], rank: int) -> EvidenceItem | None:
        chunk_id = raw.get("chunk_id", "")
        meta = self.mapping.get(chunk_id, {})
        standard_number = (
            meta.get("standard_number") or str(raw.get("standard", "")) or ""
        ).strip()
        standard_number = canonical_standard.canonicalize(standard_number) or standard_number
        section = (meta.get("section") or raw.get("section", "") or "").strip()
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

    # ---------------------------------------------------------------- 覆盖预检与过滤
    def _kb_coverage(self, canonical: str) -> tuple[bool, str]:
        """知识库是否覆盖该 Canonical Standard。

        仅精确匹配（chunk standard == canonical）视为覆盖：画像中的标准是完整标准号，
        不采用前缀匹配，避免 1910.30 误命中 1910.303 等跨标准前缀。
        """
        canonical = str(canonical or "").strip()
        if not canonical:
            return False, "none"
        if canonical in self._kb_standards:
            return True, "exact"
        return False, "none"

    @staticmethod
    def _item_matches(item_std: str, requested: list[str]) -> bool:
        """RAG 返回片段的标准是否属于请求标准家族（精确或更细子条款）。"""
        item_std = str(item_std or "").strip()
        if not item_std:
            return False
        for c in requested:
            if item_std == c:
                return True
            if item_std.startswith(c + "."):
                return True
        return False

    def _filter_by_requested(
        self, raw_items: list[dict[str, Any]], requested: list[str]
    ) -> tuple[list[dict[str, Any]], int]:
        kept: list[dict[str, Any]] = []
        rejected = 0
        for raw in raw_items:
            std = str(raw.get("standard", "")).strip()
            std_canon = canonical_standard.canonicalize(std) or std
            if self._item_matches(std_canon, requested):
                kept.append(raw)
            else:
                rejected += 1
        return kept, rejected

    def _keyword_fallback(self, standards: list[str], query_text: str) -> list[dict[str, Any]]:
        """确定性关键词回退：只在该标准的片段内做 token 打分（已由覆盖预检限定）。"""
        q_tokens = set(re.findall(r"[a-z0-9]+", str(query_text).lower()))
        scored: list[tuple[float, dict[str, Any]]] = []
        allowed = set(standards)
        for chunk in self.chunks:
            chunk_std = str(chunk.get("standard", ""))
            if chunk_std not in allowed:
                continue
            text_tokens = set(
                re.findall(r"[a-z0-9]+", f"{chunk.get('text','')} {chunk.get('section','')}".lower())
            )
            score = float(len(q_tokens & text_tokens)) if q_tokens else 1.0
            if chunk_std in standards:
                score += 10.0
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[: self.top_k]]

    # ---------------------------------------------------------------- 主入口
    def run(
        self,
        standard_codes: list[str],
        risk_categories: list[str] | None = None,
        query_id: str = "q0",
        profile_facts: list[dict[str, Any]] | None = None,
        query_text: str | None = None,
    ) -> RetrievalResult:
        risk_categories = list(risk_categories or [])
        statuses: list[dict[str, Any]] = []
        requested: list[str] = []
        covered_standards: list[str] = []

        for raw in standard_codes or []:
            req = str(raw or "").strip()
            if not req:
                continue
            canon = canonical_standard.canonicalize(req)
            if not canon:
                continue
            if canon not in requested:
                requested.append(canon)
            norm_key = canonical_standard.mapping_key(canon) or canon
            covered, mode = self._kb_coverage(canon)
            statuses.append(
                {
                    "requested_standard": req,
                    "canonical_standard": canon,
                    "normalized_standard": norm_key,
                    "retrieval_status": "covered" if covered else "coverage_gap",
                    "reason": (
                        f"知识库存在标准 {canon} 的法规片段（{mode}），允许调用学生2 RAG"
                        if covered
                        else (
                            f"知识库不存在标准 {canon} 的法规正文片段（coverage gap），"
                            "不调用语义回退，禁止伪造"
                        )
                    ),
                }
            )
            if covered and canon not in covered_standards:
                covered_standards.append(canon)

        result = RetrievalResult(
            query_id=query_id,
            standard_number=",".join(covered_standards or requested or ["UNKNOWN"]),
            risk_categories=risk_categories,
            items=[],
            empty_reason=None,
            standard_statuses=statuses,
        )

        if not requested and not query_text:
            result.empty_reason = "画像中没有历史OSHA标准编号，无法构造检索问题"
            result.standard_statuses.append(
                {
                    "requested_standard": "",
                    "canonical_standard": "",
                    "normalized_standard": "",
                    "retrieval_status": "no_standard_input",
                    "reason": "画像中没有历史OSHA标准编号，无法构造检索问题",
                }
            )
            return result

        # 纯自然语言查询（无标准输入时保留学生2已验证的语义检索能力）
        if not covered_standards:
            if query_text and self.rag is not None:
                try:
                    raw_items = self.rag.search(query_text, k=self.top_k)
                except Exception:
                    raw_items = []
                if raw_items:
                    for rank, raw in enumerate(raw_items, start=1):
                        item = self._evidence(raw, rank)
                        if item is not None:
                            result.items.append(item)
                    result.standard_statuses.append(
                        {
                            "requested_standard": "",
                            "canonical_standard": "",
                            "normalized_standard": "",
                            "retrieval_status": "natural_language",
                            "reason": "纯自然语言查询，使用学生2已验证的 BGE 语义检索（非标准编号路径）",
                        }
                    )
                    return result
            result.empty_reason = "知识库未覆盖请求的标准编号（coverage gap），禁止编造条款"
            return result

        query = query_text or " ".join(covered_standards)
        raw_items: list[dict[str, Any]] = []
        verification_rejected = 0
        if self.rag is not None:
            # 按标准逐个调用学生2 RAG（多标准拼接查询会破坏其单查询解析器，导致纯 BGE 回退）；
            # 每个查询都是单一 Canonical，走其已验证的精确过滤 + BGE 排序路径。
            for canon in covered_standards:
                try:
                    per_std = self.rag.search(canon, k=self.top_k)
                except Exception:
                    per_std = []
                per_std, rejected = self._filter_by_requested(per_std, [canon])
                verification_rejected += rejected
                raw_items.extend(per_std)
            # 保持与原行为一致的证据预算：总量不超过 top_k
            raw_items.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            raw_items = raw_items[: self.top_k]
        if not raw_items:
            raw_items = [
                {
                    "chunk_id": c.get("chunk_id", ""),
                    "standard": c.get("standard", ""),
                    "section": c.get("section", ""),
                    "text": c.get("text", ""),
                    "score": 0.0,
                }
                for c in self._keyword_fallback(covered_standards, query)
            ]

        for rank, raw in enumerate(raw_items, start=1):
            item = self._evidence(raw, rank)
            if item is not None:
                result.items.append(item)
        if verification_rejected:
            result.standard_statuses.append(
                {
                    "requested_standard": ",".join(requested),
                    "canonical_standard": ",".join(covered_standards),
                    "normalized_standard": ",".join(
                        canonical_standard.mapping_key(s) or s for s in covered_standards
                    ),
                    "retrieval_status": "verification_rejected",
                    "reason": (
                        f"学生2 RAG 返回了 {verification_rejected} 条不属于请求标准家族的片段，"
                        "已丢弃（防语义回退误命中）"
                    ),
                }
            )
        if not result.items:
            result.empty_reason = "知识库未覆盖该标准编号或查询（禁止编造条款）"
        return result
