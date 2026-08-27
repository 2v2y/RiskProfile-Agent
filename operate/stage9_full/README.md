# RiskProfile-Agent 阶段九正式实验（stage9_full · 自包含版）

阶段九目标：用**已验收（frozen）**的学生1/2/3 真实数据，公平比较 固定模板 /
普通LLM / RAG / 单Agent / 多Agent（完整 RiskProfile-Agent），并完成对比实验、
消融实验、错误分析、Bootstrap 置信区间与正式 Test 骨架。

`stage9_full` 是**完全自包含**的实验目录：代码、配置、实验数据全部在本目录内部，
运行时**不依赖**其他 stage、外部交付数据目录、用户 home 文件、GitHub 或网络下载。
唯一的外部运行时依赖是服务器上的模型资源：

- Qwen 生成模型：`/DATA/models/Qwen3.8-27B`（vLLM 服务 `http://127.0.0.1:8000/v1`）
- BGE 嵌入模型：`/DATA/models/bge-small-en-v1.5`（FAISS 检索离线加载）

## 1. 目录结构

```text
stage9_full/
├── adapters/          paths（自包含路径）/ schema_adapter / data_loader / validator /
│                      retrieval_adapter（内部 RAG）/ canonical_standard
├── baselines/         B0—B5 六个基线（沿用项目既有定义，逻辑不变）
├── config/            experiment_config.json + schema_mapping.json
│                      + agent_registry / prompt_registry / forbidden_claim_rules / prompts/
├── data/              全部冻结实验数据（profiles / knowledge / mapping / benchmark / gold）
├── evaluation/        metrics / error_analysis / bootstrap
├── experiments/       check_stage9（离线自检）/ check_qwen / run_smoke /
│                      run_comparison / run_ablation / run_error_analysis / run_test
├── results/           运行输出（不入 Git，每次运行生成唯一目录）
├── src/               自包含代码：llm（client + prompt_budget）/ common（schemas/run_log）/
│                      agents / knowledge / orchestrator / retrieval（rag_retriever）
├── tests/             test_smoke / test_canonical_standard
└── README.md
```

## 2. 数据（全部在 `data/` 内部）

| 用途 | 文件 | 版本 |
|---|---|---|
| 画像主表 | `data/profiles/profiles_train_val.csv` | 40 列冻结版，SHA `7c231b11…` |
| 8 字段补充表 | `data/profiles/profile_supplement_8fields.csv` | SHA `d7a142e3…` |
| 白名单 | `data/profiles/agent_profile_whitelist.json` | v1.1，SHA `5f2fa6a4…` |
| 法规知识库 | `data/knowledge/` | 学生2 6.0-frozen（22781 片段 / 273 标准 / 366 文档） |
| FAISS/BGE 向量库 | `data/knowledge/vector_db/` | 6.0-frozen（faiss_index.bin + chunk_ids + db_meta + embeddings.npy） |
| R1—R9 映射 | `data/mapping/standard_to_r1r9_mapping.csv` | SHA `76c0311f…` |
| 基准案例 | `data/benchmark/benchmark_cases.jsonl` | 5337 条，SHA `bd79861e…` |
| 异常案例 | `data/benchmark/red_team_cases.jsonl` | 240 条，SHA `1521faf3…` |
| 8 字段查表 | `data/benchmark/case_8fields_lookup.jsonl` | 5577 条，SHA `9c9d96a2…` |
| 评价清单 | `data/benchmark/benchmark_manifest.json` | v2.7-20260826-final |
| Ground Truth | `data/gold/benchmark_gold_restricted.jsonl` | 5337 条（受限，仅评价读取），SHA `d8b8590f…` |

数据根默认 = `stage9_full` 本身；也可用 `RP_DATA_ROOT` 指向包含同样 `data/` 布局的目录。

## 3. Baseline 定义（B0—B5 沿用项目定义，未改动）

| 方法 | 说明 | 用 LLM | 用检索 | 用 Agent | 用独立语义审查 |
|---|---|---|---|---|---|
| B0 | 固定模板 | 否 | 是 | 否 | 否 |
| B1 | 普通 LLM | 是 | 否 | 否 | 否 |
| B2 | RAG（检索增强生成） | 是 | 是 | 否 | 否 |
| B3 | 单 Agent（结构化） | 是 | 是 | 单 | 否 |
| B4 | 多 Agent（无独立语义审查） | 是 | 是 | 多 | 否 |
| B5 | 完整 RiskProfile-Agent | 是 | 是 | 多 | 是 |

## 4. RAG 与 BGE（完全离线）

- 检索实现位于 `src/retrieval/rag_retriever.py`（从学生2 6.0-frozen 交付复制，
  检索算法不变：精确过滤 + section 匹配优先 + BGE 语义排序 + 前缀匹配）；
- 知识库目录默认 `data/knowledge`（构造时传入，环境变量 `STAGE9_KNOWLEDGE_DIR` 可覆盖）；
- BGE 模型强制使用本地路径：环境变量 `BGE_MODEL_PATH` 优先，否则
  `/DATA/models/bge-small-en-v1.5`；加载时设置 `HF_HUB_OFFLINE=1`、
  `TRANSFORMERS_OFFLINE=1` 并 `local_files_only=True`，**禁止联网下载**；
- `db_meta.json` 中的 `model_name=BAAI/bge-small-en-v1.5` 只作版本记录，
  不作为加载路径；
- faiss / sentence-transformers / torch 缺失时自动回退确定性关键词检索（离线可跑，
  正式 RAG 需服务器安装依赖）。

## 5. Qwen 配置

`config/experiment_config.json` 的 `llm` 段（正式值，也可用 `.env` 覆盖）：

```json
{
  "provider": "qwen",
  "base_url": "http://127.0.0.1:8000/v1",
  "api_key": "EMPTY",
  "model": "/DATA/models/Qwen3.8-27B",
  "temperature": 0.0,
  "max_tokens": 1024,
  "timeout": 120
}
```

- model ID 必须是 vLLM 实际暴露的 `/DATA/models/Qwen3.8-27B`（含路径）；
- 调用时保留 `chat_template_kwargs.enable_thinking=false`；
- 环境变量兼容：`QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL / QWEN_TEMPERATURE /
  QWEN_MAX_TOKENS / QWEN_TIMEOUT`。

## 6. 8192 context 问题如何解决（不改实验设计）

服务器 vLLM `max_model_len=8192`，因此约束为 `input + output <= 8192`。
`src/common/prompt_budget.py` 在 **prompt 构造端**控制：

- 画像事实上限 50 条（`compact_facts`）；
- 每条法规证据正文只送前 600 字符（`compact_evidence`，引用键完整保留）；
- 画像卡超长字符串字段截断（`compact_profile`）；
- 构造完成后估算 token，超预算再渐进截断证据正文（`enforce_input_budget`）；
- 目标：`input <= 6000`，`max_tokens = 1024`，合计 `<= 7024`，留足余量。

该压缩只影响“发送给 LLM 的文本”，不影响检索结果、证据清单、评价输入
（`outputs` 中 `retrieval.items` 仍保留完整原文）。自检会实测样例 prompt：
当前验证样本估算 input ≈ 2500 tokens。

## 7. 安装依赖

```bash
cd operate/stage9_full
python -m pip install -r requirements.txt
# 服务器 RAG 需要（离线环境用预缓存 wheel 安装，禁止联网）：
python -m pip install faiss-cpu sentence-transformers torch
cp .env.example .env    # 服务器上确认 QWEN_* 与 BGE_MODEL_PATH
```

## 8. 运行流程（先离线自检，再在线验证，再正式实验）

```bash
cd operate/stage9_full

# 1) 离线自检（不调用 Qwen；检查路径/数据/import/RAG/BGE 配置/依赖）
python -m experiments.check_stage9

# 2) 服务器联通自检（真实调用 Qwen，解析 review_points JSON）
python -m experiments.check_qwen

# 3) 在线冒烟（3 个真实样本，B0—B5 全链路，provider=qwen）
python -m experiments.run_smoke --n 3

# 4) 正式实验
python -m experiments.run_comparison --split validation --limit 50     # 先小规模
python -m experiments.run_comparison --split validation                 # 全量 681
python -m experiments.run_ablation --split validation
python -m experiments.run_error_analysis --split validation
python -m experiments.run_test --test-manifest <封存清单> --confirm-test  # 正式 Test 纪律
```

离线验证（无 Qwen 时跑数据流）：

```bash
python -m experiments.run_smoke --n 3 --provider dummy
python -m pytest -v tests/           # 离线单元/冒烟测试
```

## 9. 输出文件

每次运行写入 `results/<时间戳>_<名称>/`，不覆盖旧结果：

- `run_comparison`：`rows.jsonl`、`summary.json`、`summary.csv`、`<method>/outputs.jsonl`
- `run_ablation`：`ablation_summary.csv`（指标差值 + 95% Bootstrap CI）
- `run_error_analysis`：`error_analysis.csv`、`error_analysis_summary.json`
- B5 单样本：`review_card.json` / `audit.json` / `semantic_audit.json` /
  `retrieval.json` / `profile_facts.json` / `state_trace.json` / `run_log.jsonl` /
  `output_manifest.json`

## 10. 可追溯性

- 每个运行目录内 `config.json` = 本次实验完整配置（含数据绝对路径）；
- B5 目录内 `output_manifest.json` = 输出文件 SHA-256；
- 数据版本与 SHA 见 `data/README.md` 与 `config/schema_mapping.json`；
- `results/` 与 `data/` 按项目规则不入 Git；服务器部署时整体拷贝 `stage9_full`。
