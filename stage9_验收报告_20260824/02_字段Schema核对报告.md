# Report 2：字段 Schema 核对报告

核对基准：`operate/stage9_edit/schemas/` 下四份 JSON Schema 与 `src/common/pydantic_schemas.py`（运行时校验），以及 `src/experiments/dataset_loader.py` / `retrieval_adapter.py` 的实际读取字段。状态图例：PASS / MISSING / NAME_MISMATCH / TYPE_MISMATCH / SEMANTIC_UNCERTAIN / DUPLICATE / VERSION_CONFLICT / NEED_MANUAL_CONFIRMATION。

## 1. 画像输入：profiles_train_val.csv（学生1，5337 行）

| 系统需要（ProfileCard） | 实际列（学生1 CSV） | 类型 | 状态 | 说明 |
|---|---|---|---|---|
| sample_id | sample_id | string | PASS | 唯一无重复 |
| quarter | quarter | string (YYYYQn) | PASS | 校验通过 |
| ranking_cutoff | cutoff_date | string | NAME_MISMATCH→PASS | 映射已在确认包定义，loader 转换 |
| profile_version | （无此列） | - | MISSING(已处理) | loader 硬编码 "student1-profile-v1"；学生3画像卡为 FREEZE_20260814_001，两处不一致 |
| industry_group | context_naics_group（值 2211_other 等） | string | NAME_MISMATCH→PASS | loader 用 NAICS_TO_GROUP 转 G1—G4/UNKNOWN；确认包映射写的是 candidate_naics_group→industry_group，实际 CSV 两列都有，取值相同 |
| jurisdiction_context | context_site_state（另有 candidate_site_state） | string | SEMANTIC_UNCERTAIN | 学生1 08-24 确认：同一字段改名，可直接互换；loader 读 context_site_state/candidate_site_state |
| quarter_number | quarter_number | int | PASS | |
| history_inspections / history_positive_inspections | 同名 | int | PASS | |
| smoothed_positive_rate | 同名 | float | PASS | |
| days_since_last_inspection / days_since_last_positive | 同名 | number/null | PASS | 空值保留 null + 标记 |
| inspections_365d / positives_365d / inspections_730d / positives_730d | 同名 | int | PASS | |
| decayed_inspections / decayed_positives | 同名 | float | PASS | 学生3版有 692 行浮点尾数差异 |
| 六个质量标记（no_history_flag 等） | （学生1 CSV 无此列） | bool | MISSING(已处理) | loader 派生计算；学生3画像卡已提供真实值，两者口径需确认 |
| label / label_available_date / split / activity_nrs / entity_proxy_id 等 | 存在 | 混合 | 白名单禁止 | 已在 whitelist 禁止，适配层丢弃，不进入 Agent |

## 2. 画像补充字段：profile_supplement_8fields.csv（学生1，5337 行，与画像 100% 匹配）

| 系统需要 | 实际列 | 类型 | 状态 | 说明 |
|---|---|---|---|---|
| historical_standard_codes | historical_standard_codes | string(分号分隔) | PASS | loader 转 list，含 DOL→标准转换 |
| historical_risk_categories | historical_risk_categories | string(JSON list) | PASS | 值均为 R1—R9 |
| risk_category_counts | risk_category_counts | string(JSON dict) | PASS | 键 R1—R9，无 unmapped 键 |
| risk_category_unmapped_rate | 同名 | float | PASS | |
| risk_score | risk_score | float | PASS | 0.0297—0.907，5337 行全部有值 |
| risk_percentile | risk_percentile | float | TYPE_MISMATCH→NEED_MANUAL_CONFIRMATION | 交付值 0.4—100（百分位口径），Schema 要求 0—1；loader 按 /100 转换，README 已注明需确认 |
| model_version | model_version | string | PASS | |
| model_hash | model_hash | string | VERSION_CONFLICT | 3345 行 64 位 + 1992 行 16 位截断；score_evidence 全部为完整 64 位 |
| score_evidence | score_evidence | string | PASS | 全部含 frozen_model.joblib 64 位 SHA |
| risk_categories | risk_categories | string | 多余字段 | 中文描述列，非程序输入（学生1已声明） |

## 3. 学生3 阶段4 画像卡（profiles_train_val.csv，39 列）

- 与 Schema 的关系：包含 schema 全部画像字段名（jurisdiction_context、标记组、profile_version），且与 benchmark input_card 同源（manifest 记录 sha 7c231b11…）。
- 与学生1版差异：692 行在 decayed_inspections/decayed_positives/smoothed_positive_rate 上存在浮点尾数差异；行键 (sample_id, quarter) 100% 相同。
- 状态：PASS（作为画像卡交付），但与 stage9 实际加载的学生1版为两个版本 → VERSION_CONFLICT（需确认正式画像口径）。

## 4. 学生3 benchmark 案例输入（benchmark_cases.jsonl，5337 条）— 与 Agent 输入接口核对

| 系统需要 | input_card 实际 | 状态 | 说明 |
|---|---|---|---|
| allowed_profile_facts 含历史标准/风险类别/风险计数/风险分数等 | 仅有 21 个基础画像字段 | MISSING | historical_standard_codes / historical_risk_categories / risk_category_counts / risk_category_unmapped_rate / risk_score / risk_percentile / model_version / score_evidence 全部缺失（5337/5337） |
| case_id / sample_id / quarter / split / stratification | 有 | PASS | 与画像键 100% 匹配；stratification.risk_category 分布 R1=1039/R2=4212/R8=86 |
| no_future_fields | true（全部） | PASS | |
| knowledge_version | 4.0（学生3版）/ 5.0-frozen（学生2改版） | VERSION_CONFLICT | 与 gold（4.0）及新 manifest（5.0-frozen）不一致 |
| required_outputs | profile_facts_used / regulation_chunks / unsupported_claim_check | PASS | 与阶段9输出结构可对应，但 metrics 尚未实现 |

**结论：案例集不能直接作为阶段九正式实验输入（P0）**；当前 run_baselines 直读 profiles+supplement 绕开了案例集，因此干跑能跑通。

## 5. 知识库：regulation_chunks.jsonl（学生2，4656 条）→ Evidence Schema

| Evidence Schema 需要 | 原始 chunk 字段 | 状态 | 说明 |
|---|---|---|---|
| chunk_id | chunk_id | PASS | 与 gold 引用一致 |
| standard_number / section | standard / section | NAME_MISMATCH→PASS | 适配层重命名 |
| document_id / title / source_type / source_url / effective_date / retrieved_at / is_archived | （原始无） | MISSING→PASS(适配补齐) | 由 document_inventory.csv + standard_document_mapping.csv join 补齐；13/4656（解释资料）在 stage9 适配器下 document_id=UNKNOWN |
| risk_categories | （原始无） | MISSING | stage1 适配版会按 风险分类.csv 回填；stage9 运行时直接使用画像传入的风险类别，不回填 chunk |
| evidence_id（regulation:<document_id>#<section>） | （生成） | PASS | 由 document_id/section 生成 |

## 6. 检索答案：retrieval_gold.csv（93 条，GB18030）

| 阶段1文档约定字段 | 实际字段 | 状态 |
|---|---|---|
| query_id / query_text / standard_numbers / risk_categories / gold_document_ids / gold_sections / verified_by / verified_at | 查询 / 正确标准编号 / 正确条款 / 正确片段编号 / 核对人 | NAME_MISMATCH→PASS | 知识适配器按行序生成 query_id，其余可映射；编码为 GB18030，与约定 UTF-8 不同（工具已兼容） |

## 7. 评价金标：benchmark_gold_restricted.jsonl（学生3，5337 条）

| 评价维度 | 字段 | 状态 | 说明 |
|---|---|---|---|
| case_id / sample_id / quarter / split | 有 | PASS | 与 benchmark_cases 100% 对齐（overlap=5337） |
| 正确画像数字 | expected_profile_facts_subset | PASS | 数值型（float/int），与案例 input_card 字符串型不一致，指标计算需做类型归一 |
| 正确数字/标签 | gold_label（1.0×3039 / 0.0×2298）、gold_label_available_date | PASS | |
| 法规引用 | gold_regulation_document_ids（chunk_id/standard/source_type/risk_category/evidence_available/knowledge_version） | PASS（含设计缺口） | 共 51310 条引用；29906 条有 chunk_id（全部存在于知识库），21404 条 evidence_available=false（R5/R6/R7/R9 代表标准在知识库无片段，manifest 已声明） |
| 正确风险类别 | gold_risk_categories（R1—R9 全覆盖） | PASS | |
| 安全拒绝 | expected_safe_defer_or_pass | PASS（4175 条 true / 1162 条 None） | 与证据充分性分层完全对应（insufficient→true，sufficient→None），语义已明确；只需在 manifest 写明规则并确认 |
| knowledge_version | 4.0（全部） | VERSION_CONFLICT | 与学生2新cases（5.0-frozen）不一致 |

## 8. 关联键核对（学生1→学生3→学生2）

| 关联 | 结果 | 说明 |
|---|---|---|
| profiles_train_val (sample_id, quarter) ↔ supplement | 5337/5337（100%） | 无重复键、无空键 |
| benchmark_cases (sample_id, quarter) ↔ profiles | 5337/5337（100%） | |
| gold case_id ↔ benchmark_cases case_id | 5337/5337（100%） | |
| red_team case_id | 240 唯一 | 10 类×24，expected_outcome 全部 defer_or_reject |
| gold chunk_id ↔ regulation_chunks | 0 个缺失 | 29906 条有 chunk_id 的引用全部存在 |
| chunk standard ↔ document_inventory | 13/4656 缺失（解释资料） | stage9 适配器下 document_id=UNKNOWN |
| standard_to_r1r9_mapping ↔ 风险分类.csv 最终主类 | R6: 179 vs 0；R9: 2664 vs 3257 | 溯源不一致，需人工确认 |
| 学生1版画像 ↔ 学生3版画像 | 692 行浮点差异 | 精度口径需统一 |
