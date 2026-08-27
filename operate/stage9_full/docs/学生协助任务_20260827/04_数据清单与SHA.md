# 数据清单与 SHA（Stage9 正式实验）

所有数据位于 `~/RiskProfile-Agent/operate/stage9_full/data/`，已全部随 Git 提交
（含 `data/gold/` 基准答案），`git pull` 即可获得。SHA-256 为完整值，用于核对版本。

## 画像 / 映射（学生1 负责）

| 文件 | SHA-256 | 大小 | 说明 |
|---|---|---:|---|
| `data/profiles/profiles_train_val.csv` | 7c231b119631157a3b9b4f17422d3f7be5fbff878198618e24614c11026e3992 | 1.4 MB | 40 列冻结画像，5337 行 |
| `data/profiles/profile_supplement_8fields.csv` | d7a142e3b0538bedc3db35c6df0bb3556fd5ea19166d0d8f6fbe900d3e96140f | 1.7 MB | 8 字段补充表，5337 行 |
| `data/profiles/agent_profile_whitelist.json` | 5f2fa6a4f44b70d3693b28c9dcfd3b7795838483bc5ab4019055dbcb55ca4f1a | 11 KB | 白名单 v1.1 |
| `data/mapping/standard_to_r1r9_mapping.csv` | 76c0311fe83097f8f72b4f1688db6503199659c9f2b9b54c52721047da444d66 | 610 KB | R1–R9 权威映射，6346 行 |

## 知识库 / 检索（学生2 负责）

| 文件 | SHA-256 | 大小 | 说明 |
|---|---|---:|---|
| `data/knowledge/chunks/regulation_chunks.jsonl` | 8508ad1c8d6bf6e93adb14752fe7b870ce0026264c13711eb877acd8ced5169d | 8.7 MB | 6.0-frozen，22781 片段 |
| `data/knowledge/standard_document_mapping.csv` | 79b36ffb61f19c0a04cd87e616bc4a4f0f373c37c96d6c4b6a82a5c314dc45a9 | 2.7 MB | 片段→文档对应表 |
| `data/knowledge/document_inventory.csv` | fec83a322f6fe0403fcff7d35921ecd6a05e908a2b1c6f7292b95cef555543de | 145 KB | 文档清单，366 文档 |
| `data/knowledge/knowledge_manifest.json` | cd9f3db76b88500e320f034f5808b9c9b2e2a4fe8fd3bbc8b1067dca7e2a5285 | 3 KB | 6.0-frozen 清单 |
| `data/knowledge/retrieval_gold.csv` | 5172d38349b0f4a57adf146d096cae350a6366b622e7952ab3da59ab642470a9 | 5 KB | 检索验证集 |
| `data/knowledge/retrieval_validation_metrics.csv` | 7a8c61b563c1fd8ed56d3d2dd849474d47b4280f2986ead25ca936f5c7bebb60 | 1 KB | 检索指标 |
| `data/knowledge/vector_db/faiss_index.bin` | 7c4258d6c46843717ba355eb04d92356f289cebdafcb5cc216cb7a7c866c8d3e | 35 MB | FAISS 索引（22781×384） |
| `data/knowledge/vector_db/chunk_ids.json` | 70345dfd62468189562227158da97e1996ae6ea3297641e1ee6bda2ae728304f | 342 KB | chunk id 映射 |
| `data/knowledge/vector_db/db_meta.json` | 4660b4b9f0b95d003f36c0627448c90440f15748b0ebad9e2e0589f8570eb131 | 390 B | 索引元数据（model_name 仅版本记录） |
| `data/knowledge/vector_db/embeddings.npy` | 6ce1c6a52ce976cb785a67391970b5fe3710aa42adb6e90a02e1c66a16212968 | 35 MB | 向量原始数据 |

## 基准 / 评价（学生3 交付，已冻结）

| 文件 | SHA-256 | 大小 | 说明 |
|---|---|---:|---|
| `data/benchmark/benchmark_cases.jsonl` | bd79861e5ade00e1c5f7cce9283b49087e4e38ecc2e823deb5800bf9098456d1 | 8.6 MB | 5337 条案例 |
| `data/benchmark/red_team_cases.jsonl` | 1521faf3ce1c5ec2ef82bc2c93867797c400c409838fa4a1c8743d6ccacc3c27 | 354 KB | 240 条异常案例 |
| `data/benchmark/case_8fields_lookup.jsonl` | 9c9d96a26b7744efb34eb4291af3887c42bc46b391a8dd010f2de9330b5f1882 | 2.5 MB | 5577 条查表 |
| `data/benchmark/benchmark_manifest.json` | 0a315a69ea0a3584112a810abc1c045e17388198c36810f9eed492125390124f | 23 KB | v2.7-20260826-final |
| `data/gold/benchmark_gold_restricted.jsonl` | d8b8590f68c4fbc2be7b06dcdff7c9c972aad9d57ef849232d9a64019f7ed961 | 12.7 MB | 基准答案，仅评价阶段读取 |

## 服务器模型资源（运行时依赖，不复制）

| 模型 | 路径 | 用途 |
|---|---|---|
| Qwen | `/DATA/models/Qwen3.8-27B`（vLLM `http://127.0.0.1:8000/v1`） | 生成模型，max_model_len=8192 |
| BGE | `/DATA/models/bge-small-en-v1.5` | 检索嵌入，384 维，离线加载 |
