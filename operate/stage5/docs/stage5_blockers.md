# 阶段5 阻塞与待确认清单

记录日期：2026-08-20

## 已完成

- 从学生1 交付复制冻结产物到 `data/frozen/`：frozen_model.joblib、frozen_config.json、frozen_manifest.json、model_selection.json。
- 从学生1 的 `03_fit_validate.py` / `pipeline_common.py` 逆向出 M2 结构与特征（context + static + dynamic，逻辑回归 C=1.0、max_iter=2000、liblinear）。
- 写好打分脚本 `src/risk_model/score_profiles.py`（R0/M1/M2 打分 + 季度分位 + 版本/来源字段）。

## 无法执行的部分（需要你拍板）

1. **缺少带 scikit-learn 的 Python 环境**
   - 当前可用运行时是 Python 3.12，没有 sklearn/scipy/joblib。
   - 学生1 的旧 venv（Python 3.13.12）其基础解释器被系统拒绝访问，无法直接调用。
   - 影响：无法加载 frozen_model.joblib，因此 risk_score / risk_percentile 等 4 个字段没有实际回填。
   - 待你决定：在服务器或本地提供一个带 scikit-learn 的 Python 3.11/3.13 环境，并 `pip install -r stage5/requirements.txt`。

2. **字段名与语义：context_site_state vs jurisdiction_context**
   - 冻结 M2 用 `context_site_state` 作为 context 特征；
   - 学生3 新版画像把该列改名为 `jurisdiction_context`，且字段字典把含义写成"监管背景（联邦/州计划分类）"，而旧字典写"截点前最近州别"。
   - 两者当前取值看起来相同（都是州缩写如 HI），但语义是否等价需要学生3/导师确认。
   - 待你决定：是否把 jurisdiction_context 直接重命名回 context_site_state 用于 M2 打分。

3. **M2 打分用的画像版本**
   - 冻结模型是用学生1 的旧版 profiles_train_val.csv（含 context_site_state）训练的；
   - 学生3 的新版 profiles_train_val.csv（40 列）可用于打分，但必须做上面的字段对齐。
   - 待你决定：正式打分用哪一版画像，以及是否重新生成风险分数后由学生3 合并回画像卡。

## 下一步

1. 确定带 sklearn 的环境；
2. 确认 jurisdiction_context 与 context_site_state 的等价性；
3. 运行 `python -m src.risk_model.score_profiles --profiles <画像CSV> --out data/profiles_with_score.csv`；
4. 把 score_evidence 换成 frozen_model.joblib 的真实 SHA-256（见 frozen_manifest.json）。
