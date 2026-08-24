# RiskProfile-Agent 阶段9实验（stage9_edit）

本目录按研究方案 §31.2 推荐结构组织，目标是实现 B0—B5 六个基线、统一干跑、指标与消融骨架。

## 目录结构

- `configs/`：阶段9配置、白名单、Registry 和禁止规则；
- `data/02_train_validation/`：学生1画像与风险补充字段；
- `knowledge/`：学生2法规知识库、FAISS 向量库和检索代码；
- `benchmark/`：学生3自动评价集和异常输入集；
- `schemas/`：四类 JSON Schema；
- `src/experiments/`：阶段9实验代码；
- `runs/`：不可覆盖的运行输出；
- `manifests/`、`paper/`：按方案预留。

## 复用关系

`stage9_edit` 与 `stage1` 位于同一仓库的 `operate/` 下。阶段9实验会复用 `stage1` 里的画像整理、复核建议、内容审查和流程控制器模块；`src/experiments/paths.py` 会自动把 `../stage1` 加入 Python 路径。

## 数据来源

- 学生1：`profiles_train_val.csv`、`profile_supplement_8fields.csv`；
- 学生2：`regulation_chunks.jsonl`、`document_inventory.csv`、`standard_document_mapping.csv`、`retrieval_gold.csv`、FAISS 向量库；
- 学生3：`benchmark_cases.jsonl`、`red_team_cases.jsonl`。

## 离线干跑

```bash
cd operate/stage9_edit
python -m src.experiments.run_baselines --n 5 --methods B0,B1,B2,B3,B4,B5
```

默认 `llm.provider=dummy`，用于验证六个方法都能落盘。真正调用 Qwen 时，把 `configs/stage9_config.json` 中：

```json
"review": { "use_llm": true },
"llm": { "provider": "qwen" }
```

并在 `.env` 中配置 `QWEN_BASE_URL`、`QWEN_MODEL`。

## 已知问题

- 正式指标需要学生3的 `benchmark_gold_restricted.jsonl`，当前 `metrics.py` 只提供骨架。
- 学生1 `risk_percentile` 交付值为 0—100，代码按 /100 转成 Schema 要求的 0—1，正式使用前需确认。
- R1—R9 映射以 `standard_to_r1r9_mapping.csv` 为准，白名单中旧名称待学生1修正。
