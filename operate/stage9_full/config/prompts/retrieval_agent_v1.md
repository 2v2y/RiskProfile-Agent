# retrieval_agent 规则说明 v1（2026-08-27，Stage9 自包含版）

Retrieval Agent 是**确定性规则模块（不使用 LLM）**，本文件是其可追溯的规则说明
（等价于该 Agent 的“Prompt”），供论文/评审引用，不参与运行时推理。

## 职责
1. 根据历史 OSHA 标准编号、风险类别和画像原子事实构造检索请求；
2. 在已冻结知识库（`data/knowledge/`，6.0-frozen）中检索，返回
   document_id / standard_number / section / text / score；
3. 找不到足够证据时返回空结果和明确失败原因，禁止编造标准号/条款/URL/法规内容。

## 检索链路（Stage9 自包含实现）
1. 标准编号先经 `adapters/canonical_standard.py` 统一为 Canonical Standard
   （如 `19260651 A` / `1926.0651` → `1926.651`）；
2. 知识库覆盖预检：**只有精确 Canonical 覆盖**才调用
   `src/retrieval/rag_retriever.py`（FAISS + 本地 BGE `/DATA/models/bge-small-en-v1.5`，
   离线加载）；无覆盖返回 `coverage_gap`，禁止语义回退误命中；
3. RAG 返回片段按请求标准家族过滤，丢弃无关片段（verification_rejected 记录）；
4. RAG 不可用时回退确定性关键词检索（仅在该标准片段内打分）。

## 参数（冻结）
- `top_k = 3`、`fallback = keyword`、`method = student2-hybrid-faiss-bge`
- 知识库 6.0-frozen：22781 片段 / 273 标准 / 366 文档

## 输出 Schema
- `src/common/pydantic_schemas.py::RetrievalResult`
  （含逐标准 `standard_statuses` 审计字段）
