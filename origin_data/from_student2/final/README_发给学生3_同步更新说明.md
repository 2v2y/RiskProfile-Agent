# 给学生3：知识库 6.0-frozen 同步更新说明（final 包）

整理日期：2026-08-26　整理方：学生4（Stage 9 集成）
用途：学生3 据此同步更新 `benchmark_cases.jsonl` / `red_team_cases.jsonl` /
`benchmark_gold_restricted.jsonl` / `benchmark_manifest.json`。

## 一、已定决策（本包的口径）

1. **R1-R9 风险分类以学生1最终版映射为唯一权威**：`standard_to_r1r9_mapping.csv`
   （SHA-256 `76c0311f…`，含 R6=179；**起重机 1926.1400-1443 维持 R8**）。
   学生2提出的"起重机统一改 R6"版本（1657 包）**未采用**，本包不含该版本。
2. **知识库 = 6.0-frozen**：22,781 片段 / 273 标准 / 366 文档（学生2 `交付_学生4修正`）。
3. gold 的 R1-R9 代表标准口径不变：**R8 代表标准仍为 `1926.1402`（R8）**，无需调整。

## 二、本包文件清单与 SHA-256

| 文件 | 说明 | SHA-256 |
|---|---|---|
| `standard_to_r1r9_mapping.csv` | R1-R9 权威映射（学生1最终版，起重 R8） | `76c0311fe83097f8f72b4f1688db6503199659c9f2b9b54c52721047da444d66` |
| `knowledge/knowledge_manifest.json` | 知识库版本 6.0-frozen、366 文档/22,781 片段 | `cd9f3db76b88500e320f034f5808b9c9b2e2a4fe8fd3bbc8b1067dca7e2a5285` |
| `knowledge/regulation_chunks.jsonl` | 法规片段正文（22,781 条） | `8508ad1c8d6bf6e93adb14752fe7b870ce0026264c13711eb877acd8ced5169d` |
| `knowledge/standard_document_mapping.csv` | 标准编号↔片段对应表（22,781 行） | `79b36ffb61f19c0a04cd87e616bc4a4f0f373c37c96d6c4b6a82a5c314dc45a9` |
| `knowledge/document_inventory.csv` | 文档台账（366 行） | `fec83a322f6fe0403fcff7d35921ecd6a05e908a2b1c6f7292b95cef555543de` |
| `knowledge/db_meta.json` | 向量库版本 6.0-frozen、22781×384（版本佐证） | `4660b4b9f0b95d003f36c0627448c90440f15748b0ebad9e2e0589f8570eb131` |
| `代表标准chunk对照表_6.0frozen.csv` | 3 个新覆盖代表标准的真实 chunk 清单（590 行） | — |

## 三、学生3必须改的 3 件事

### 1. `knowledge_version` → `6.0-frozen`

以下三个文件全部改（当前是 5.0-frozen，另清理根目录 4.0 旧副本）：

- `benchmark_cases.jsonl`（5,337 条）
- `red_team_cases.jsonl`（240 条）
- `benchmark_gold_restricted.jsonl`（5,337 条）

### 2. gold 的 `evidence_available` 重标 + 补真实 chunk_id（10,730 条）

6.0-frozen 已补齐 3 个代表标准片段，gold 中对应引用目前 `evidence_available=False`、
`chunk_id="None"`，需改为：

| gold 标准（映射键） | Canonical | 新库片段数 | 引用数 | 改为 |
|---|---|---|---|---|
| 1910.0036 | 1910.36（R5） | 29 | 5,337 | `evidence_available=True` + 真实 chunk_id |
| 1910.0179 | 1910.179（R6） | 271 | 5,337 | `evidence_available=True` + 真实 chunk_id |
| 1910.0146 | 1910.146（R7） | 290 | 56 | `evidence_available=True` + 真实 chunk_id |
| 1910.0001 | 1910.1 | 0 | 5,337 | 维持 `False`（知识库无片段） |
| 1926.0202 | 1926.202 | 0 | 5,337 | 维持 `False`（知识库无片段） |

真实 chunk_id 取自 `代表标准chunk对照表_6.0frozen.csv`（1910.36×29、1910.179×271、
1910.146×290），也可用 `knowledge/regulation_chunks.jsonl` 自行核对。

### 3. `benchmark_manifest.json` 的 sources 更新

- `sources.knowledge_manifest`：193 文档/4,656 片段 → **366 文档/22,781 片段**，版本 6.0-frozen；
- `sources.standard_document_mapping`：SHA 更新为 `79b36ffb…`，版本 6.0-frozen；
- `knowledge_version` → `6.0-frozen`；登记本包各文件 SHA。

## 四、明确不用改的

- R1-R9 代表标准清单（R8=1926.1402 在新口径下仍为 R8，自洽）；
- `gold_risk_categories`、`expected_safe_defer_or_pass`、`stratification`；
- `case_8fields_lookup.jsonl`（学生1补充表未变，无需重新生成）；
- 画像输入（学生1 `profiles_train_val.csv` + `profile_supplement_8fields.csv` 未变）。

## 五、回传要求（改完后交回学生4）

1. 更新后的三个文件 + manifest 的新 SHA-256；
2. 校验记录：gold 中 `evidence_available=True` 的 chunk_id ⊆ 新库 chunks，**0 缺失**；
3. 重标后 True 引用总数 = 29,906 + 10,730 = **40,636**（False 维持 10,674）。

## 六、本包未包含（学生3不需要）

- 学生2 1657 包的"起重机改 R6"映射（未采用）；
- `rag_retriever.py`、`vector_db/`（faiss / embeddings / chunk_ids）——服务器集成用；
- `retrieval_validation_metrics.csv`、`retrieval_gold.csv`——学生4服务器验证用。
