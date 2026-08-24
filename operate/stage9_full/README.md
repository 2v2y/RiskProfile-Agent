# RiskProfile-Agent 阶段九正式实验（stage9_full）

阶段九目标：用**已经验收**的学生1/2/3真实数据，公平比较 固定模板 / 普通LLM / RAG / 单Agent / 多Agent（完整 RiskProfile-Agent），并完成对比实验、消融实验、错误分析、Bootstrap 置信区间与正式 Test 骨架。

本目录从 `stage9_edit`（实验框架）升级而来，复用阶段八（`operate/stage1`）已验证的 Agent 代码，只负责实验编排与数据接入，不重写 RiskProfile-Agent 本体。

## 1. 目录结构

```text
stage9_full/
├── config/            experiment_config.json（统一配置）、schema_mapping.json（字段映射记录）
├── data/README.md     真实数据说明（数据不入 Git）
├── adapters/          数据适配层：paths / schema_adapter / data_loader / validator / retrieval_adapter
├── baselines/         B0—B5 六个基线（复用阶段八 Agent）
├── evaluation/        metrics / error_analysis / bootstrap
├── experiments/       run_smoke / run_comparison / run_ablation / run_error_analysis / run_test
├── results/           运行输出（不入 Git，每次运行生成唯一目录）
├── tests/             test_smoke
├── README.md          本文档
└── UNRESOLVED.md      需人工确认的问题
```

## 2. 数据来源

全部引用仓库内 `origin_data/` 中已验收数据（路径见 `config/experiment_config.json` 的 `data` 段；服务器可用环境变量 `RP_DATA_ROOT` 覆盖数据根目录）。

| 学生 | 数据 | 用途 |
|---|---|---|
| 学生1 | `profiles_train_val.csv` + `profile_supplement_8fields.csv` + `agent_profile_whitelist.json`(v1.1) | Profile Agent 输入（画像卡 + 8字段补充 + 白名单） |
| 学生2 | `knowledge/`（chunks / inventory / mapping / retrieval_gold / vector_db）+ `rag_retriever.py` | Retrieval Agent 的法规知识库与检索 |
| 学生3 | `benchmark_cases.jsonl` + `red_team_cases.jsonl` + `benchmark_gold_restricted.jsonl` + `benchmark_manifest.json` | 评价样本、Ground Truth、评价口径 |

数据只读，实验代码**不修改**任何已验收数据；字段差异统一在 `adapters/schema_adapter.py` 做映射，映射规则记录在 `config/schema_mapping.json`。

R1—R9 风险分类的权威映射以学生2交付的最新版 `standard_to_r1r9_mapping.csv`（6346 行）为准（见 `config/experiment_config.json` 的 `data.r1r9_mapping`）。运行链路直接使用学生1补充表中已算好的 `historical_risk_categories`，该映射文件作为分类口径的可追溯权威引用。

## 3. Baseline 定义（沿用项目 B0—B5）

| 方法 | 说明 | 用 LLM | 用检索 | 用 Agent | 用独立语义审查 |
|---|---|---|---|---|---|
| B0 | 固定模板 | 否 | 是 | 否 | 否 |
| B1 | 普通 LLM | 是 | 否 | 否 | 否 |
| B2 | RAG（检索增强生成） | 是 | 是 | 否 | 否 |
| B3 | 单 Agent（结构化） | 是 | 是 | 单 | 否 |
| B4 | 多 Agent（无独立语义审查） | 是 | 是 | 多 | 否 |
| B5 | 完整 RiskProfile-Agent | 是 | 是 | 多 | 是 |

所有方法使用同一批画像输入、同一套知识库、同一份 Ground Truth 与评价规则。

## 4. 完整 Agent 定义

固定流程（阶段八 `operate/stage1` 已实现并验证）：Profile → Retrieval → Review → Audit（确定性核对 + 独立语义审查）→ PASS / DEFER（转人工） / REJECT。stage9_full 复用它，并把 Retrieval 换成学生2 FAISS/BGE 检索（缺依赖时自动回退关键词），证据输出符合 `evidence_schema`（含 evidence_id / document_id / standard / section / source / score）。

## 5. 评价指标

实现于 `evaluation/metrics.py`，逐样本比较 Agent 输出与 Ground Truth：

- `numeric_accuracy`：画像数字与 gold 一致的比例（容差可配）
- `citation_validity`：引用是否真实存在于检索结果
- `citation_correctness`：引用标准是否命中 gold 标准
- `evidence_support` / `unsupported_claim`：有/无依据陈述占比
- `traceability`：证据引用能否回溯到画像事实或检索证据
- `safe_refusal`：与 gold `expected_safe_defer_or_pass` 对齐的安全拒绝/通过

只读取 Ground Truth 做比较，**绝不**根据结果修改 gold 或测试集。

## 6. 消融实验

`experiments/run_ablation.py`：完整系统（B5）对比去掉某模块的变体（去掉独立语义审查=B4、去掉检索=B1、去掉审计=B2），输出各指标差值与 Bootstrap 95% 置信区间。

## 7. Error Analysis

`evaluation/error_analysis.py` 把每样本归类到 NUMERIC_ERROR / CITATION_ERROR / EVIDENCE_UNSUPPORTED / UNSUPPORTED_CLAIM / OUT_OF_SCOPE / REFUSAL_ERROR / OTHER，保留样本ID。

## 8. Bootstrap / CI

`evaluation/bootstrap.py`：配对 Bootstrap 计算两方法指标差异的 95% 置信区间。

## 9. Smoke Test

```bash
python -m experiments.run_smoke --n 2
```

先用 1—3 个样本验证 Profile→Retrieval→Review→Audit→Evaluation 全链路，再跑正式实验。

## 10. 正式 Test

`experiments/run_test.py` 遵循开封纪律：没有封存 Test 清单和 `--confirm-test` 时拒绝运行；Test 数据封存于受限目录，不在 Git 仓库。

## 11. 如何配置

- 实验参数：`config/experiment_config.json`
- 字段映射：`config/schema_mapping.json`
- 敏感信息：复制 `.env.example` 为 `.env`（Qwen 地址/Key/模型名；`RP_DATA_ROOT` 数据根）
- 不把 `.env`、API Key、密码提交 Git。

离线干跑默认 `llm.provider=dummy`（确定性假模型，不联网）。正式运行把 `llm.provider` 设为 `qwen` 并在 `.env` 配置。

## 12. 如何在服务器运行

```bash
git clone https://github.com/2v2y/RiskProfile-Agent.git   # 或 git pull
cd RiskProfile-Agent/operate/stage9_full
python -m pip install -r requirements.txt                  # 学生2检索器需 faiss-cpu/sentence-transformers/torch
cp .env.example .env                                       # 填 Qwen 与数据根
export RP_DATA_ROOT=/path/to/real/data                     # 指向真实数据根（若不放仓库默认位置）
python -m experiments.run_smoke --n 3
python -m experiments.run_comparison --split validation --limit 100
python -m experiments.run_ablation --split validation --limit 100
python -m experiments.run_error_analysis --split validation --limit 100
```

代码不依赖 Windows 本地路径、用户名或 IDE；路径由 config/env 控制。

## 13. 输出文件

每次运行写入 `results/<时间戳>_<名称>/`，不覆盖旧结果：

- `run_comparison`：`rows.jsonl`、`summary.json`、`summary.csv`、`<method>/outputs.jsonl`
- `run_ablation`：`ablation_summary.csv`
- `run_error_analysis`：`error_analysis.csv`、`error_analysis_summary.json`

## 14. 如何复现

1. 确认数据文件存在（见 `data/README.md`）且为已验收版本；
2. 用相同 `experiment_config.json` 与 `.env`；
3. 从 smoke 到 comparison/ablation/error 按上面命令顺序运行；
4. 正式实验固定 `random_seed`、`llm.model`、样本集与 gold 版本，并记录 commit 与数据 SHA。

## 15. 与 stage9_edit 的关系

`stage9_edit` 不再作为正式实验目录。`stage9_full` 是阶段九正式实验目录：完整代码 + 实验配置 + 已确认数据接入（数据本体按规则不入 Git）。
