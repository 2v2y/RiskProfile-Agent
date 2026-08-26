# 未决问题清单（不能自行决定，需人工确认）

说明：以下问题属于“代码/数据/口径尚需人确认”，按 31 节要求记录，不在代码中擅自拍板。

## 1. B2（RAG）与 B4（多 Agent 无独立语义审查）在离线 dummy 下输出近似

- 相关文件：`baselines/base.py`
- 当前观察：B2 与 B4 都走“检索 + ReviewAgent(LLM) + 确定性 Audit”，dummy LLM 下二者输出几乎一致；其差别是架构语义（B2 为单次 RAG 生成，B4 为显式多 Agent 分解、无独立语义审查），真实差异要到接入 Qwen、引入分解后的证据账本才会显现。
- 影响：对比实验里 B2/B4 的区分度。
- 候选方案：(a) 保留现有 B0—B5 定义，在 README 写清架构差异；(b) 若导师要求更严格区分，再调整 B2/B4 的实现差异。
- 为什么无法确定：B0—B5 的“方法定义”来自项目既有命名，需要导师/学生4 最终拍板。
- 需要人工确认什么：B2 与 B4 的最终方法学定义是否按现状即可。

## 2. 离线 dummy LLM 下 citation_correctness=0（占位）

- 相关文件：`evaluation/metrics.py`、`baselines/base.py` 的 `FakeLLM`
- 当前观察：dummy 假模型不输出标准编号，故“引用正确率”恒为 0，属占位结果。
- 影响：离线跑出的引用类指标无实际意义，仅验证框架可算。
- 候选方案：正式实验必须把 `llm.provider=qwen`（配置 `.env`）后重跑。
- 为什么无法确定：真实 Qwen 输出未接入，本地无法得到真实引用。
- 需要人工确认什么：正式实验在服务器用 Qwen 运行，离线数字不作为结论。

## 3. R1—R9 映射权威版本 —— 已决议（2026-08-25 更新）：以学生1交付版为唯一 Canonical Standard

- 决议：R1—R9 权威映射采用**学生1 08-24 最终版** `origin_data/from_student1/回复学生4_20260824/回复学生4_20260824/standard_to_r1r9_mapping.csv`（6346 行，SHA `76c0311f…`，含 R6=179）。`config/experiment_config.json` 的 `data.r1r9_mapping` 已同步切换。
- 原因：以学生1交付的标准体系作为 Stage 9 唯一 Canonical Standard（见 `docs/standard_consistency_analysis.md`）。画像 `risk_category_counts` / `historical_risk_categories` 本身就是按学生1口径生成（实测含 R6，如 1910.212 → R6 共 85 行），与学生2包内旧映射（R6=0、R8=777）不一致；stage9_验收报告 06 也判定学生2包内映射为旧版需替换。
- 记录的事实（供追溯）：学生1版 R6=27 个唯一标准（179 行映射键）、R8=152；学生2包内版 R6=0、R8=179，二者差异仅 27 个标准的 R6/R8 归属。
- 运行说明：stage9_full 运行链路仍直接使用学生1补充表已算好的 `historical_risk_categories`；该映射用于标准一致性审计与复算（`experiments/audit_standard_consistency.py`、`tests/test_canonical_standard.py`）。

## 4. `historical_standard_codes` 约 37% 空值

- 相关文件：学生1 `profile_supplement_8fields.csv`（1992/5337 行空）
- 当前观察：空值样本检索会返回“无历史标准编号”，按失败关闭转人工（DEFER）。
- 影响：这些样本的检索维度无法评价（安全转人工是否“对”取决于 gold 口径）。
- 候选方案：学生1 后续补齐；或评价时对空值样本单独统计。
- 需要人工确认什么：空值样本是否按“证据不足→应 DEFER”口径评价。

## 5. gold 为“行业组参考代表标准”而非实体真实标准

- 相关文件：学生3 `benchmark_manifest.json` notes
- 当前观察：gold 法规引用为行业组参考标准；R5/R6/R7/R9 代表标准在知识库无片段（`evidence_available=false`）。
- 影响：citation_correctness 的口径需说明（缺失引用不应计为普通引用错误）。
- 需要人工确认什么：论文/指标中如何表述该口径。

## 6. 正式 Test 数据不在仓库

- 相关文件：`experiments/run_test.py`
- 当前观察：正式 Test 使用封存数据，仓库不包含；入口只做纪律校验并拒绝伪造。
- 需要人工确认什么：封存 Test 数据的存放位置与开封流程（阶段11）。

## 7. [已解决] 学生2知识库覆盖缺口（6.0-frozen 已补齐 1926.651 等）→ 剩余两类小缺口

- 状态：学生2 6.0-frozen（`origin_data/from_student2/交付_学生4修正`）已把知识库扩充到
  22,781 片段 / 273 标准，画像 144 个联邦标准 **144/144 覆盖**（含 1926.651×37、1926.652×54）；
  stage9 config 已切换到该版本。原"1926.651 覆盖缺失"问题已解决。
- 剩余缺口（已核验）：
  1. **gold 代表标准 1910.1（R7）与 1926.202（R9）在知识库无片段**：共 10,674 条引用维持
     `evidence_available=False`（学生3已按此重标）；需学生2确认补齐或论文按缺失引用口径说明。
  2. **画像 210 条验证样本无历史标准编号**（学生1已回填 183 行，剩余为真实无历史记录实体）+
     **109 条含州法规代码不在联邦知识库覆盖范围**：按"证据不足→DEFER"口径处理。
- 处理方式：适配层已对无覆盖标准返回 `coverage_gap`（`retrieval.standard_statuses`），不伪造、
  不语义回退误命中；检索类指标按有证据子集（验证集 362/681）统计。
