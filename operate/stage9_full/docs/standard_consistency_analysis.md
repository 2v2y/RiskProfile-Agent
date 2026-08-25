# Stage 9 标准编号一致性分析（Canonical Standard 决议）

日期：2026-08-25
范围：`operate/stage9_full` 全链路（画像 → R1–R9 映射 → RAG 检索 → 实验输出）
结论先行：**1926.651 的问题同时包含「标准编号不一致」与「知识库覆盖不足」两类根因**；学生1交付中定义的
Canonical Standard 取 **OSHA 官方引用格式（如 `1926.651`）**，学生2知识库使用同一格式；R1–R9 映射表内的
`standard_normalized`（如 `1926.0651`）是**映射查找键**，不是 Canonical Standard 本身。

---

## 1. 学生1交付扫描结果（实际执行，非猜测）

### 1.1 候选文件

| 文件（学生1 最终交付） | SHA-256 | 内容 | 在本项目中的作用 |
|---|---|---|---|
| `回复学生4_20260824/回复学生4_20260824/standard_to_r1r9_mapping.csv` | `76c0311f…` | 6346 行 R1–R9 映射（含 R6=179） | **R1–R9 映射权威版**（验收报告 P1 决议，也是画像 risk_category_counts 的生成口径） |
| `回复学生4_20260824/回复学生4_20260824/风险分类_R6修正.csv` | `3496d3c2…` | 8347 条逐条裁定记录 | 映射的生成源；含 `standard原值` / `standard规范值` / `官方标准说明入口` |
| `回复学生4_20260824/回复学生4_20260824/profile_supplement_8fields.csv` | `d0f1dbc9…` | 5337 行 8 字段补充表 | Stage 9 画像输入；`historical_standard_codes` 为 DOL 原值 |
| `回复学生4_20260824/回复学生4_20260824/agent_profile_whitelist.json` | `5f2fa6a4…` | 白名单 v1.1 | 定义 `historical_standard_codes` = “OSHA标准编号（standard）的集合” |
| `学生1/共同材料/04_风险分类规则.md` | — | 分类规则 | 要求“规范化 standard，但原始值不得覆盖”，未定义编号格式 |
| `学生1/数据_分析数据/violation_clean.csv` | — | 45678 行违章 | `standard` 列 = DOL 原值（如 `19260651 A`） |

### 1.2 学生1交付中同一标准的三种表示（以 1926.651 为例，全量统计）

| 表示 | 例子 | 出现位置（学生1交付内） |
|---|---|---|
| DOL 原值 | `19260651 A` / `19260651 J02` | `violation_clean.csv`（925 次）、`profile_supplement_8fields.csv`（219 次）、`风险分类_R6修正.csv` 的 `standard原值`（53 次） |
| 映射键（standard_normalized） | `1926.0651` | `standard_to_r1r9_mapping.csv`（33 行）、`风险分类_R6修正.csv` 的 `standard规范值`（53 次） |
| 官方引用格式 | `1926.651` | `风险分类_R6修正.csv` 的 `官方标准说明入口` URL（`standardnumber/1926/1926.651`）与初分/复核/裁定依据（106 次）、映射 `basis`（33 行） |

### 1.3 学生1映射键的生成规则（对全部 3032 行 1910/1926 数字码验证，0 例外）

```text
mapping_key = 前4位前缀 + "." + (4位零填充节号).rstrip("0")
例：19260651 → 1926.0651    19260501 → 1926.0501    19260960 → 1926.096
    19100132 → 1910.0132    19100030 → 1910.003     19260100 → 1926.01
```

该键是**有损**的：`1926.106` 这一键实际来自 `19261060`（官方 `1926.1060`），与 `19260106`（官方 `1926.106`）
是两个不同标准；`1910.003` 实际是 `19100030`（官方 `1910.30`）。因此映射键不能反向当作 Canonical Standard。

### 1.4 Canonical Standard 决议

**Canonical Standard = OSHA 官方引用格式：`part + "." + str(int(节号))`（节号去前导零，保留全部有效位）。**

证据链：
1. 学生1 `风险分类_R6修正.csv` 的 `官方标准说明入口` 使用官方 URL `…/standardnumber/1926/1926.651`；
2. 学生1 两名标注者的依据文字使用 `1926.651`（初分/复核/裁定，106 处）；
3. 学生1 交付的映射 `basis` 使用 `1926.651`；
4. 学生2 `学生4检索程序接入指南.md` 明文规定知识库标准格式为 `1910.269 / 1926.1050`，转换方法为“去掉前 4 位前缀，剩余部分去前导零，加回点号”（即与 Canonical 一致）；
5. 学生2 `rag_retriever.py::_convert_standard` 用 `str(int(code[4:]))` 去前导零；
6. 学生2 `regulation_chunks.jsonl` / `standard_document_mapping.csv` 的 `standard` / `OSHA标准编号` 均为官方引用格式（100 个唯一标准，如 `1910.132`、`1926.501`、`1926.960`、`1926.1053`）；
7. Stage 9 现有 `schema_adapter.convert_standard` 对 DOL 原值已输出官方引用格式（`19260651 J02` → `1926.651`），但未处理 `1926.0651` 这类映射键输入。

非 1910/1926 编号（州法规 `16VAC25-60-130`、加州 `024005670103`、`408.22141(2)` 等）不属于 OSHA 编号体系，
**不进行转换**，原样保留并单独标记为非联邦标准。

---

## 2. 学生2知识库检查结果（实际统计）

### 2.1 知识库结构（版本 5.0-frozen）

| 文件 | 行数 | 标准格式 |
|---|---|---|
| `regulation_chunks.jsonl` | 4656 | `standard` 官方引用格式（100 个唯一标准） |
| `standard_document_mapping.csv` | 4656 | `OSHA标准编号` 与 chunks 100% 一致 |
| `document_inventory.csv` | 193 | `OSHA标准编号` 官方引用格式 |
| `retrieval_gold.csv` | 93 | 查询 + 正确标准编号（含截断编号，如 `1926.105`） |
| `retrieval_validation_metrics.csv` | 12 | hybrid k=3: Recall=1.0000 / MRR=0.8530 |

### 2.2 1926.651 专项核查

| 检查项 | 结果 |
|---|---|
| chunks `standard == 1926.651` | **0** |
| chunks `section == 1926.651` | **0** |
| chunks `text` 含 `1926.651` / `1926.0651` / `1926.65` | **0** |
| chunks `standard == 1926.0651` | **0** |
| mapping / inventory 中 1926.651 变体 | **0** |
| 1926.65x 家族（开挖 Subpart P）任何片段 | **0** |

结论：**学生2法规正文知识库确实没有 1926.651（开挖）的正文片段**，属于覆盖范围缺失，不是单纯字段名/格式问题。

### 2.3 全量覆盖对比（画像 655 个 Canonical 标准 vs 知识库 100 个标准）

| 口径 | 数量 |
|---|---|
| 画像联邦标准（1910/1926） | 144 个（5343 次出现） |
| 其中知识库已覆盖 | 44 个 |
| **其中知识库缺失** | **100 个（3723 次出现）** |
| 画像非联邦标准 | 511 个（2882 次出现，均不在知识库，属预期） |
| 知识库有、画像没有的标准 | 56 个（如 1926.1050、1926.1400–1441 部分） |

知识库覆盖集中度：1910.132–138（PPE）、1910.147（LOTO）、1910.269（电力）、1910.303–333（电气）、
1926.501–502（防坠）、1926.950–967（输电）、1926.1050–1060（梯子/楼梯）、1926.1400–1441（起重）。
**1926.4xx（电气）、1926.451–454（脚手架）、1926.6xx（开挖/吊装/混凝土）、1926.8xx 等均未覆盖。**

---

## 3. 当前 Stage 9 代码中的问题

| 位置 | 问题 | 影响 |
|---|---|---|
| `adapters/schema_adapter.py::convert_standard` | 只处理 `1910.xxx/1926.xxx` 与 8 位 DOL 码；`1926.0651` 会被原样返回，不统一 | 画像/映射/检索各层标准表示不一致 |
| `adapters/retrieval_adapter.py::run` | 检索前虽然调 convert_standard，但直接拼接后交给学生2 RAG；没有 KB 覆盖预检 | 若 RAG 可用，`1926.651` 会落入纯 BGE 语义回退，返回不相关片段（fallback 误命中） |
| `adapters/retrieval_adapter.py::_keyword_fallback` | 空标准时对所有 chunks 做 token 打分 | 可能绕过标准限制产生误命中 |
| 无标准级可追溯日志 | `RetrievalResult` 只有 `empty_reason`，无法区分“哪个标准缺失、规范化成什么” | 无法审计、无法复现 1926.651 的完整链路 |
| `config/experiment_config.json` `data.r1r9_mapping` | 指向学生2包内映射（SHA `fe623738…`，R6=0）；学生1最终版（`76c0311f…`，R6=179）才是画像分类口径 | 审计/复算时 R6 标准（如 1910.212 机械防护）会错位 |

另外：学生2 `学生4检索程序接入指南.md` 中“`19261050` → `1926.1050`”的示例在映射表中并不存在
（映射无 `19261050` 行），说明该文档示例不可作为映射键规则依据；已用全量数据验证真实规则（见 1.3）。

---

## 4. 设计决议：Canonical Standard 层

```text
raw standard（DOL 原值 / 映射键 / 官方格式）
    ↓  canonical_standard.canonicalize()
Canonical Standard（官方引用格式，如 1926.651）
    ↓  canonical_standard.mapping_key()
R1–R9 映射查找键（standard_normalized，如 1926.0651）
    ↓  同一 Canonical Standard
学生2 RAG（知识库 chunk 标准即官方引用格式）
    ↓
Stage 9 实验输出（retrieval.standard_statuses 逐标准记录）
```

实现位置：`adapters/canonical_standard.py`（新）。

### 4.1 检索行为变化（防 fallback 误命中）

1. 检索前对每个标准做 KB 覆盖预检（exact 或与学生2一致的前缀匹配）；
2. 有覆盖 → 用 Canonical 查询学生2 RAG（不修改学生2算法/索引/模型）；
3. 无覆盖 → **不调用学生2 RAG、不返回语义近似片段**，逐标准记录 `coverage_gap`；
4. `RetrievalResult` 增加可选字段 `standard_statuses`，每条含
   `requested_standard / canonical_standard / normalized_standard / retrieval_status / reason`；
5. RAG 返回的 items 做归属校验，不属于请求标准家族的直接丢弃并记录 `verification_rejected`。

### 4.2 R1–R9 映射权威版本切换

按“学生1标准体系为唯一 Canonical”的决议，`config/experiment_config.json` 的 `data.r1r9_mapping`
改为学生1最终版 `76c0311f…`（与画像 `risk_category_counts` 口径一致，含 R6），并更新 `UNRESOLVED.md`
原第 3 条决议记录。运行时链路仍直接使用画像已算好的 `historical_risk_categories`，该映射用于审计与复算。

---

## 5. 需人工/上游补充的数据缺口（BLOCKER / DATA GAP）

1. **[DATA GAP] 学生2知识库缺开挖/Subpart P 正文**：`1926.651`、`1926.652` 等 1926.6xx 标准无任何片段。
   学生2需补充法规正文并重建向量索引；在补齐前 Stage 9 如实返回 `coverage_gap`，不伪造。
2. **[DATA GAP] 知识库总体覆盖不足**：画像 144 个联邦标准中 100 个无片段（3723 次出现）。
   建议学生2按画像标准分布扩充知识库，或正式实验明确限定“仅对知识库覆盖标准评价检索维度”。
3. **[DATA QUALITY] 映射键有损**：`standard_normalized` 的 rstrip("0") 规则使 `1926.106`（实为 1926.1060）
   与 `1926.0106`（实为 1926.106）等键容易误读；映射键只用于查找，不对外作为标准编号。
4. **[DATA QUALITY] 学生1映射含重复键**：如 `1926.0025` 与 `1926.025`、`1926.0055`(R7) 与 `1926.055`(R6)
   等成对键对应不同 DOL 原码长度，R 类别可能不同；审计脚本会列出全部冲突对，不擅自选择。
5. **[DATA QUALITY] 学生2交付说明示例错误**：`19261050 → 1926.1050` 示例在映射中不存在，需学生2更正说明或
   补充该标准映射行。

---

## 6. 不修改的冻结文件（明确清单）

- `origin_data/from_student1/**`（全部只读引用）
- `origin_data/from_student2/**`（含 `regulation_chunks.jsonl`、`standard_document_mapping.csv`、
  `document_inventory.csv`、`retrieval_gold.csv`、`retrieval_validation_metrics.csv`、`vector_db/*`、
  `rag_retriever.py`）
- `origin_data/from_student3/**`
- 已有 `results/**`（历史运行输出，不覆盖）

所有统一逻辑只发生在 Stage 9 的 `adapters/` 层与审计/测试脚本中。

---

## 7. 审计实测结果（2026-08-25，`experiments/audit_standard_consistency.py`）

```text
=== STANDARD CONSISTENCY AUDIT ===
Canonical standards (student1 mapping): 2866
Profile standards: 655 (federal 144, non-federal 511)
Student2 mapping standards: 2866
Knowledge chunk standards: 100 (chunks 4656)
Profile ∩ Knowledge: 44
Profile standards missing from knowledge: 611 (federal 100)
Mapping standards missing from knowledge (exact): 2802 (even prefix: 2795)
Unknown profile standards (non-federal, unmapped): 0
R category diff (student1 vs student2 mapping): 27（全部为 S1=R6 vs S2=R8）
Ambiguous mapping keys: 0
1926.651 special check: chunks=0, mapping S1=33/S2=33, profile=219
```

说明：
- 画像 655 个标准 100% 命中学生1映射（2866 个 Canonical 标准之一），说明画像与映射口径一致；
- 知识库 100 个标准中画像只用到 44 个（联邦标准 144 个里只有 44 个有正文），**覆盖缺口是真实数据问题**；
- 学生1/学生2 映射差异恰好 27 个机械类标准（R6 vs R8），与验收报告 P1 结论一致；
- 审计 JSON 输出在 `results/audit_standard_consistency/<时间戳>/audit.json`。
