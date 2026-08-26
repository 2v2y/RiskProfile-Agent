"""
RAG检索接口（混合检索版）
基于BGE向量 + FAISS索引 + 精确过滤
供学生4接入检索程序时使用

检索策略：
  - 标准编号查询（如"1910.132"）→ 精确过滤 + BGE语义排序
  - 自然语言查询（如"fall protection"）→ 纯BGE语义检索
  - DOL格式查询（如"19100132 Q01"）→ 自动转换格式后检索

验证结果（93条验证集，最终版）：
  - Recall@3 = 1.0000（100%命中）
  - Precision@3 = 0.3333
  - MRR = 0.8530
  - NDCG@10 = 0.8914
  改进点：1)标准编号查询时优先匹配section字段；
  2)用float比较处理编号截断(如1926.105 vs 1926.1050)。

使用方法：
    from rag_retriever import RAGRetriever

    retriever = RAGRetriever()
    results = retriever.search("1910.132", k=3)
    for r in results:
        print(r["chunk_id"], r["standard"], r["score"], r["text"][:100])
"""

import os
import re
import json
import shutil
import tempfile
import numpy as np


def _read_faiss_index(path):
    """读取FAISS索引，兼容含中文/非ASCII的路径。

    FAISS的C++文件IO（fopen）不支持非ASCII路径，直接读含中文的路径
    会报"could not open ... No such file or directory"。遇到该情况时，
    先把索引复制到系统临时目录（纯ASCII路径）再读取。
    """
    import faiss
    try:
        return faiss.read_index(path)
    except RuntimeError:
        tmp_dir = os.path.join(tempfile.gettempdir(), "rag_faiss_cache")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, "faiss_index.bin")
        shutil.copyfile(path, tmp_path)
        return faiss.read_index(tmp_path)


class RAGRetriever:
    """
    RAG检索器：混合检索（精确过滤 + BGE语义排序）

    检索流程：
    1. 判断查询类型（标准编号 or 自然语言）
    2. 如果是标准编号：
       a. 转换格式（DOL格式 → 知识库格式）
       b. 精确过滤出所有匹配的法规片段
       c. 在匹配片段中用BGE语义排序
    3. 如果是自然语言：
       a. 用BGE编码查询
       b. FAISS全局搜索最相似的片段
    4. 返回top-k结果
    """

    def __init__(self):
        """初始化：加载FAISS索引、BGE模型、法规片段"""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        knowledge_dir = os.path.join(base_dir, "knowledge")
        db_dir = os.path.join(knowledge_dir, "vector_db")

        # 加载FAISS索引（_read_faiss_index兼容含中文的路径）
        self.index = _read_faiss_index(os.path.join(db_dir, "faiss_index.bin"))

        # 加载chunk_ids映射
        with open(os.path.join(db_dir, "chunk_ids.json"), "r", encoding="utf-8") as f:
            self.chunk_ids = json.load(f)

        # 加载元信息
        with open(os.path.join(db_dir, "db_meta.json"), "r", encoding="utf-8") as f:
            self.meta = json.load(f)

        # 加载BGE模型
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(self.meta["model_name"])

        # 加载法规片段
        self.chunks = {}
        chunks_path = os.path.join(knowledge_dir, "chunks", "regulation_chunks.jsonl")
        with open(chunks_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunk = json.loads(line)
                    self.chunks[chunk["chunk_id"]] = chunk

        # 预建标准编号索引（加速精确过滤）
        self._std_index = {}
        for idx, cid in enumerate(self.chunk_ids):
            chunk = self.chunks.get(cid, {})
            std = chunk.get("standard", "")
            if std:
                self._std_index.setdefault(std, []).append(idx)

    def _is_standard_number(self, query):
        """判断查询是否是标准编号格式（如1910.132）"""
        query = query.strip()
        if re.match(r'^(1910|1926)\.\d+', query):
            return True
        if re.match(r'^(1910|1926)\d{4,}', query):
            return True
        return False

    def _convert_standard(self, raw):
        """DOL格式转标准格式：19100132 → 1910.132"""
        raw = raw.strip()
        parts = raw.split()
        if not parts:
            return raw
        code = parts[0]
        if not code.isdigit() or len(code) < 7:
            return raw
        part = code[:4]
        section = str(int(code[4:]))
        return f"{part}.{section}"

    def _std_match(self, chunk_std, target_std):
        """标准编号匹配：精确匹配或前缀匹配"""
        if chunk_std == target_std:
            return True
        if chunk_std.startswith(target_std):
            return True
        return False

    def _section_match(self, section, query):
        """section字段与查询匹配：精确匹配或数值等价（处理1926.105 vs 1926.1050）"""
        if section == query:
            return True
        try:
            return float(section) == float(query)
        except (ValueError, TypeError):
            return False

    def _hybrid_search(self, query, k=3):
        """
        混合检索核心逻辑：
        - 标准编号：精确过滤 + section精确匹配优先 + BGE排序
        - 自然语言：纯BGE检索
        """
        std_query = query.strip()

        # DOL格式转换
        if " " in std_query and any(c.isdigit() for c in std_query):
            converted = self._convert_standard(std_query)
            if self._is_standard_number(converted):
                std_query = converted

        # 标准编号查询：精确过滤 + section匹配优先 + BGE排序
        if self._is_standard_number(std_query):
            matched_indices = []

            # 先精确匹配
            matched_indices = self._std_index.get(std_query, [])

            # 精确匹配为空，尝试前缀匹配（处理1926.105→1926.1050的截断）
            if not matched_indices:
                for std, indices in self._std_index.items():
                    if std.startswith(std_query):
                        matched_indices.extend(indices)

            if matched_indices:
                query_vec = self.model.encode(
                    [query], normalize_embeddings=True
                ).astype('float32')

                matched_vectors = np.array([
                    self.index.reconstruct(idx) for idx in matched_indices
                ]).astype('float32')

                bge_scores = matched_vectors @ query_vec[0]

                # 复合排序：(section精确匹配, BGE分数)
                # section与查询完全匹配的片段优先排在前面
                ranked = []
                for bge_score, idx in zip(bge_scores, matched_indices):
                    chunk_id = self.chunk_ids[idx]
                    chunk = self.chunks.get(chunk_id, {})
                    section = chunk.get('section', '')
                    section_match = 1 if self._section_match(section, std_query) else 0
                    ranked.append((section_match, float(bge_score), idx))

                ranked.sort(key=lambda x: (-x[0], -x[1]))
                return [(s, idx) for _, s, idx in ranked[:k]]

        # 自然语言查询：纯BGE检索
        query_vec = self.model.encode(
            [query], normalize_embeddings=True
        ).astype('float32')

        scores, indices = self.index.search(query_vec, k)
        return [(float(s), int(idx)) for s, idx in zip(scores[0], indices[0])]

    def search(self, query, k=3):
        """
        检索法规片段（混合检索）

        参数：
            query: 查询内容，支持三种格式：
                   - 标准编号："1910.132"
                   - DOL格式："19100132 Q01"
                   - 自然语言："fall protection equipment"
            k: 返回条数（默认3，已验证的最佳值）

        返回：
            list of dict，每条包含：
                - chunk_id: 片段编号
                - standard: 标准编号
                - section: 条款编号
                - text: 法规正文
                - score: 相似度得分（0~1，越高越相关）
        """
        raw_results = self._hybrid_search(query, k=k)

        results = []
        for score, idx in raw_results:
            chunk_id = self.chunk_ids[idx]
            chunk = self.chunks.get(chunk_id, {})
            results.append({
                "chunk_id": chunk_id,
                "standard": chunk.get("standard", ""),
                "section": chunk.get("section", ""),
                "text": chunk.get("text", ""),
                "score": score,
            })

        return results

    def search_by_standard(self, standard_number, k=3):
        """
        按标准编号检索（自动处理格式转换）

        参数：
            standard_number: 标准编号，支持两种格式
                            "1910.132"（知识库格式）
                            "19100132 B01"（DOL API格式）
            k: 返回条数

        返回：同 search()
        """
        return self.search(standard_number, k=k)


# 测试代码
if __name__ == "__main__":
    print("=" * 60)
    print("RAG检索器测试（混合检索版）")
    print("=" * 60)

    retriever = RAGRetriever()
    print(f"\n知识库：{len(retriever.chunks)} 条法规片段")
    print(f"标准编号索引：{len(retriever._std_index)} 个标准")
    print(f"模型：{retriever.meta['model_name']}")

    # 测试1：标准编号查询
    print("\n--- 测试1：标准编号查询 ---")
    results = retriever.search("1910.132", k=3)
    for i, r in enumerate(results, 1):
        print(f"  #{i} 得分={r['score']:.4f} | {r['chunk_id']} | {r['standard']} {r['section']}")
        print(f"     {r['text'][:80]}...")

    # 测试2：自然语言查询
    print("\n--- 测试2：自然语言查询 ---")
    results = retriever.search("fall protection personal protective equipment", k=3)
    for i, r in enumerate(results, 1):
        print(f"  #{i} 得分={r['score']:.4f} | {r['chunk_id']} | {r['standard']} {r['section']}")
        print(f"     {r['text'][:80]}...")

    # 测试3：DOL格式查询
    print("\n--- 测试3：DOL格式查询 ---")
    results = retriever.search("19100132 Q01", k=3)
    for i, r in enumerate(results, 1):
        print(f"  #{i} 得分={r['score']:.4f} | {r['chunk_id']} | {r['standard']} {r['section']}")
        print(f"     {r['text'][:80]}...")

    # 测试4：被截断的编号（验证前缀匹配）
    print("\n--- 测试4：截断编号查询（前缀匹配验证）---")
    results = retriever.search("1926.105", k=3)
    for i, r in enumerate(results, 1):
        print(f"  #{i} 得分={r['score']:.4f} | {r['chunk_id']} | {r['standard']} {r['section']}")
        print(f"     {r['text'][:80]}...")

    print("\n" + "=" * 60)
    print("测试完成！")
