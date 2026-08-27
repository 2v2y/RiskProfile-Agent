"""法规检索模块（Retrieval Agent）。

阶段8实现：
1. 根据历史OSHA标准编号、风险类别和画像原子事实构造检索请求（build_query）；
2. 调用已冻结知识库（regulation_chunks.jsonl + standard_document_mapping.csv）；
3. 候选集限定在"标准编号 -> 官方文档片段"固定对应表内（学生2阶段6交付），
   再用 TF-IDF 余弦相似度排序，返回 top_k=3（检索方法与条数已按阶段6冻结）；
4. 找不到足够证据时返回空结果和明确失败原因，禁止编造标准号/条款/URL/法规内容。

全程确定性实现（无LLM、无随机性），保证同一输入得到同一输出。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import EvidenceItem, RetrievalResult
from src.knowledge.adapter import RISK_CATEGORY_NAMES, convert_standard


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


class RetrievalAgent:
    def __init__(
        self,
        chunks_path: Path | str,
        mapping_path: Path | str | None = None,
        top_k: int = 3,
        min_score: float = 1.0,
        method: str = "tfidf-standard-restricted",
    ):
        self.chunks_path = Path(chunks_path)
        self.mapping_path = Path(mapping_path) if mapping_path else None
        self.top_k = top_k
        self.min_score = min_score
        self.method = method
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
        if self.mapping_path is None or not Path(self.mapping_path).exists():
            return {}
        mapping: dict[str, str] = {}
        with open(self.mapping_path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                std = row["standard_number"].strip()
                mapping.setdefault(std, row["document_id"].strip())
        return mapping

    @staticmethod
    def _standard_matches(standard: str, chunk_standard: str) -> bool:
        """标准编号匹配：完全相等或层级前缀匹配（如 1910.269 匹配 1910.269(a)(1)）。"""
        return standard == chunk_standard or chunk_standard.startswith(standard + ".") or standard.startswith(chunk_standard + ".")

    def build_query(
        self,
        standard_codes: list[str],
        risk_categories: list[str] | None = None,
        profile_facts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """构造检索请求：历史OSHA标准编号 + 风险类别 + 画像原子事实引用。"""
        risk_categories = list(risk_categories or [])
        parts: list[str] = []
        if standard_codes:
            parts.append("历史涉及OSHA标准编号：" + "、".join(standard_codes))
        if risk_categories:
            names = [RISK_CATEGORY_NAMES.get(cat, cat) for cat in risk_categories]
            parts.append("历史风险类别：" + "、".join(names))
        fact_refs = [f.get("provenance", "") for f in (profile_facts or []) if f.get("provenance")]
        if fact_refs:
            parts.append("画像事实引用：" + "、".join(fact_refs[:20]))
        return {
            "standard_numbers": list(standard_codes or []),
            "risk_categories": risk_categories,
            "query_text_zh": "；".join(parts) if parts else "（无检索信号）",
            "profile_fact_refs": fact_refs,
        }

    def _tfidf_rank(
        self,
        candidates: list[dict[str, Any]],
        standard_codes: list[str],
        risk_categories: list[str],
    ) -> list[tuple[float, dict[str, Any]]]:
        """标准限定候选集内的 TF-IDF 余弦排序（确定性，无随机性）。"""
        import numpy as np

        tokenized = [
            _tokenize(c.get("text", "")) + _tokenize(c.get("title", ""))
            for c in candidates
        ]
        query_tokens: list[str] = []
        for std in standard_codes:
            query_tokens += _tokenize(std)
        for cat in risk_categories:
            query_tokens += _tokenize(cat)
            query_tokens += _tokenize(RISK_CATEGORY_NAMES.get(cat, ""))
        query_tokens = list(dict.fromkeys(query_tokens))

        if not query_tokens:
            scored = []
            for c in candidates:
                bonus = 2.0 * len(set(risk_categories) & set(c.get("risk_categories", [])))
                scored.append((10.0 + bonus, c))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            return scored

        vocab = sorted(set(query_tokens).union(*[set(t) for t in tokenized]))
        vocab_idx = {tok: i for i, tok in enumerate(vocab)}
        v_len = len(vocab)
        n_docs = len(tokenized)
        matrix = np.zeros((n_docs, v_len), dtype=np.float64)
        doc_freq = np.zeros(v_len, dtype=np.float64)
        for i, toks in enumerate(tokenized):
            counts: dict[str, int] = {}
            for t in toks:
                if t in vocab_idx:
                    counts[t] = counts.get(t, 0) + 1
            if not toks:
                continue
            for t, cnt in counts.items():
                matrix[i, vocab_idx[t]] = cnt / len(toks)
                doc_freq[vocab_idx[t]] += 1.0

        idf = np.log((1.0 + n_docs) / (1.0 + doc_freq)) + 1.0
        matrix *= idf

        query_vec = np.zeros(v_len, dtype=np.float64)
        for t in query_tokens:
            query_vec[vocab_idx[t]] += 1.0
        query_vec *= idf
        query_norm = float(np.linalg.norm(query_vec))

        scored: list[tuple[float, dict[str, Any]]] = []
        for i, c in enumerate(candidates):
            doc_norm = float(np.linalg.norm(matrix[i]))
            cosine = (
                float(np.dot(matrix[i], query_vec) / (doc_norm * query_norm))
                if doc_norm > 0 and query_norm > 0
                else 0.0
            )
            bonus = 2.0 * len(set(risk_categories) & set(c.get("risk_categories", [])))
            scored.append((round(cosine * 10.0 + bonus, 3), c))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def run(
        self,
        standard_codes: list[str],
        risk_categories: list[str] | None = None,
        query_id: str = "q0",
        profile_facts: list[dict[str, Any]] | None = None,
    ) -> RetrievalResult:
        risk_categories = list(risk_categories or [])
        standard_codes = [convert_standard(c) or c for c in (standard_codes or [])]
        query = self.build_query(standard_codes, risk_categories, profile_facts)
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

        candidates: list[dict[str, Any]] = []
        for chunk in self._chunks:
            chunk_std = str(chunk.get("standard_number", ""))
            if any(self._standard_matches(std, chunk_std) for std in standard_codes):
                candidates.append(chunk)

        if not candidates:
            result.empty_reason = f"知识库未覆盖标准编号：{standard_codes}（禁止编造条款）"
            return result

        scored = self._tfidf_rank(candidates, standard_codes, risk_categories)
        picked = [c for s, c in scored if s >= self.min_score][: self.top_k]
        if not picked:
            result.empty_reason = "检索得分低于阈值，未找到足够证据"
            return result

        result.items = []
        for rank, chunk in enumerate(picked, start=1):
            document_id = chunk.get("document_id", "")
            section = str(chunk.get("section", ""))
            score = next(s for s, c in scored if c is chunk)
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
                    score=score,
                    rank=rank,
                )
            )
        return result
