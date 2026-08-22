# RiskProfile-Agent 阶段5：冻结确定性排序底座

目标：用学生1 冻结的 R0/M1/M2 模型，给画像卡补充 `risk_score`、`risk_percentile`、`model_version`、`score_evidence`。

本阶段不修改学生1 的冻结模型，只读取 `data/frozen/` 下的冻结产物并做推理。

## 当前状态

- 已把学生1 的冻结产物复制到 `data/frozen/`（frozen_model.joblib、frozen_config.json、frozen_manifest.json、model_selection.json）。
- 已写好打分脚本 `src/risk_model/score_profiles.py`。
- **尚未实际跑分**：当前可用 Python 运行时没有 scikit-learn，无法加载 frozen_model.joblib。见 `docs/stage5_blockers.md`。

## 依赖

需要带 scikit-learn 的 Python 环境（建议与冻结模型环境一致，见 frozen_manifest.json / environment_versions.json）。
