"""法规检索模块（Retrieval Agent）。

根据历史OSHA标准编号和风险类别检索官方知识库，返回证据条目；
找不到足够证据时返回空结果和原因，禁止编造条款。
阶段1使用关键词/BM25-lite 确定性实现，正式对比（关键词 vs 向量 vs 混合）在阶段6/9进行。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import EvidenceItem, RetrievalResult


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class RetrievalAgent:
    def __init__(
        self,
        chunks_path: Path | str,
        mapping_path: Path | str | None = None,
        top_k: int = 3,
        min_score: float = 1.0,
    ):
        self.chunks_path = Path(chunks_path)
        self.mapping_path = Path(mapping_path) if mapping_path else None
        self.top_k = top_k
        self.min_score = min_score
        self._chunks = self._load_chunks()
        self._mapping = self._load_mapping()

    def _load_chunks(self) -> list[dict[str, Any]]:
        chunks = []
        with open(self.chunks_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return chunks

    def _load_mapping(self) -> dict[str, str]:
        if self.mapping_path is None or not self.mapping_path.exists():
            return {}
        mapping: dict[str, str] = {}
        with open(self.mapping_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mapping[row["standard_number"].strip()] = row["document_id"].strip()
        return mapping

    @staticmethod
    def _standard_matches(standard: str, chunk_standard: str) -> bool:
        """标准编号匹配：完全相等或层级前缀匹配（如 1910.269 匹配 1910.269(a)(1)）。"""
        return standard == chunk_standard or chunk_standard.startswith(standard + ".") or standard.startswith(chunk_standard + ".")

    def run(
        self,
        standard_codes: list[str],
        risk_categories: list[str] | None = None,
        query_id: str = "q0",
    ) -> RetrievalResult:
        risk_categories = risk_categories or []
        result = RetrievalResult(
            query_id=query_id,
            standard_number=",".join(standard_codes) if standard_codes else "UNKNOWN",
            risk_categories=risk_categories,
            items=[],
            empty_reason=None,
        )

        if not standard_codes:
            result.empty_reason = "画像中没有历史OSHA标准编号，无法构造检索问题"
            return result

        scored: list[tuple[float, dict[str, Any]]] = []
        for chunk in self._chunks:
            chunk_std = str(chunk.get("standard_number", ""))
            exact_hit = any(self._standard_matches(std, chunk_std) for std in standard_codes)
            if not exact_hit:
                continue  # 未命中标准编号的片段不进入证据，避免数字巧合误检
            score = 10.0
            chunk_cats = set(chunk.get("risk_categories", []))
            score += 2.0 * len(set(risk_categories) & chunk_cats)
            text_tokens = _tokenize(str(chunk.get("text", "")) + " " + str(chunk.get("title", "")))
            query_tokens = set()
            for std in standard_codes:
                query_tokens |= _tokenize(std)
            for cat in risk_categories:
                query_tokens |= _tokenize(cat)
            score += float(len(query_tokens & text_tokens))
            scored.append((score, chunk))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        picked = [c for s, c in scored if s >= self.min_score][: self.top_k]

        if not picked:
            unknown = [s for s in standard_codes if s not in self._mapping]
            result.empty_reason = (
                f"知识库未覆盖标准编号：{unknown}"
                if unknown
                else "检索得分低于阈值，未找到足够证据"
            )
            return result

        result.items = []
        for rank, chunk in enumerate(picked, start=1):
            document_id = chunk["document_id"]
            section = str(chunk.get("section", ""))
            result.items.append(
                EvidenceItem(
                    evidence_id=f"regulation:{document_id}#{section}",
                    document_id=document_id,
                    standard_number=chunk.get("standard_number", ""),
                    section=section,
                    title=chunk.get("title", ""),
                    text=chunk.get("text", ""),
                    source_type=chunk.get("source_type", "regulation"),
                    source_url=chunk.get("source_url", ""),
                    effective_date=chunk.get("effective_date"),
                    retrieved_at=chunk.get("retrieved_at", ""),
                    is_archived=bool(chunk.get("is_archived", False)),
                    score=round(score, 3),
                    rank=rank,
                )
            )
        return result
