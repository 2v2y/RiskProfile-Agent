# Stage9 数据说明（自包含）

本目录是 Stage9 正式实验的唯一数据来源：`stage9_full/data/` 内部已放入全部
已验收（frozen）的学生1/2/3 实验数据，`config/experiment_config.json` 的
`data.*` 全部指向本目录，**不再依赖仓库内外部交付目录、其他 stage 或外网**。

## 目录布局

| 子目录 | 内容 | 冻结版本 / SHA-256 前缀 |
|---|---|---|
| `profiles/` | `profiles_train_val.csv`（40 列）、`profile_supplement_8fields.csv`、`agent_profile_whitelist.json` | `7c231b11…` / `d7a142e3…` / `5f2fa6a4…` |
| `knowledge/` | 学生2 6.0-frozen 知识库：`chunks/regulation_chunks.jsonl`（22781）、`standard_document_mapping.csv`、`document_inventory.csv`、`knowledge_manifest.json`、`retrieval_gold.csv`、`retrieval_validation_metrics.csv`、`standard_to_r1r9_mapping.csv`、`vector_db/`（faiss_index.bin + chunk_ids.json + db_meta.json + embeddings.npy） | 6.0-frozen |
| `mapping/` | `standard_to_r1r9_mapping.csv`（R1—R9 权威映射） | `76c0311f…` |
| `benchmark/` | `benchmark_cases.jsonl`（5337）、`red_team_cases.jsonl`（240）、`case_8fields_lookup.jsonl`（5577）、`benchmark_manifest.json`（v2.7） | `bd79861e…` / `1521faf3…` / `9c9d96a2…` |
| `gold/` | `benchmark_gold_restricted.jsonl`（5337，受限，仅评价阶段读取） | `d8b8590f…` |

## 数据根解析

1. 环境变量 `RP_DATA_ROOT` 若设置，须指向**包含同样 `data/` 布局**的目录；
2. 否则默认 = `stage9_full` 本身（即使用本目录）。

## 数据安全约定

- `gold/benchmark_gold_restricted.jsonl` 是“考试答案”，与案例输入物理隔离，只在评价阶段读取；
- 本目录已随 Git 提交（`data/` 其余部分）；唯一例外是 `data/gold/`
  （受限考试答案，见 `stage9_full/.gitignore`），需单独拷贝到服务器；
- `results/` 仍不入 Git；
- 不把 `.env`、服务器密钥提交 Git。

## 版本来源（追溯）

- 知识库 `knowledge_manifest.json` 为 6.0-frozen（366 文档 / 22781 片段 / 273 标准）；
- `db_meta.json` 中 `model_name = BAAI/bge-small-en-v1.5` **仅作版本记录**，运行时
  一律用服务器本地模型 `/DATA/models/bge-small-en-v1.5`（离线加载）；
- 案例 / red_team / gold / manifest 的 `knowledge_version` 均为 6.0-frozen。
