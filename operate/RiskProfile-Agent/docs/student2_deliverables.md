# 学生2 → 学生4 交接清单（法规知识库与风险分类）

依据：《RiskProfile-Agent 研究方案》第 10 节、14.3 节、31.9 节（阶段6），以及 RiskProfile-Agent.md 中学生2的分工。

这份清单只列学生4（Agent 系统与实验）真正会用到的东西，按"文件格式 + 表头/字段 + 交付时间"写清楚。

---

## 一、总体：学生2 要交接什么

学生2 在阶段6 负责建设官方知识库和法规检索的验证材料，最终要交给你 7 类东西：

1. 风险分类表（R1—R9，锁定版）
2. `document_inventory.csv`：官方文件清单
3. `regulation_chunks.jsonl`：按条款切分好的法规正文
4. `knowledge_manifest.json`：知识库版本与校验值
5. `standard_document_mapping.csv`：标准编号 → 官方条款对照表
6. `retrieval_gold.csv`：人工核对的"查询问题 → 正确法规"答案
7. `retrieval_validation_metrics.csv`：不同检索方法在验证集上的比较结果

其中 1—6 是学生2 直接产出；第 7 项由学生2 出 gold，你（学生4）接入检索程序后共同算出并回填。

---

## 二、每个文件的具体格式

### 1. 风险分类表（R1—R9）

用途：把历史 OSHA 标准编号映射到风险类别，画像、检索和建议卡都要用它。

- 文件：`risk_category_mapping.csv`（文件名可与学生2/3协商，字段必须齐全）
- 格式：CSV，UTF-8，首行为表头
- 表头：
  - `standard_number`：OSHA 标准编号，如 `1910.269`
  - `risk_category`：R1—R9
  - `risk_category_name_zh`：中文类别名，如"电气危险与带电防护"
  - `rule_version`：分类规则版本
  - `notes`：备注（可为空）

R1—R9 中文含义（方案 8.9）：

| 类别 | 含义 |
|---|---|
| R1 | 电气危险与带电防护 |
| R2 | 个人防护装备 |
| R3 | 能量隔离与上锁挂牌 |
| R4 | 高处作业与坠落 |
| R5 | 培训、许可和程序执行 |
| R6 | 机械设备和工器具 |
| R7 | 受限空间、消防和危险环境 |
| R8 | 现场通道、物体打击和综合防护 |
| R9 | 其他或尚未分类 |

约束：这个表必须锁定版本，并记录 SHA-256；"未映射"的记录要能单独统计（对应画像里的 `risk_category_unmapped_rate`）。

### 2. `document_inventory.csv`

用途：列出知识库里每份官方文件的名称、网址、日期、版本和校验值，作为知识库的"总目录"。

- 格式：CSV，UTF-8，首行为表头
- 表头：
  - `document_id`：文档唯一编号
  - `title`：文件标题
  - `source_type`：来源类型，取值 `regulation` / `interpretation` / `data_definition` / `field_manual` / `archive`
  - `source_url`：可回到官方原文的链接
  - `effective_date`：生效日期（可为空）
  - `retrieved_at`：本项目下载日期
  - `is_archived`：是否已归档（TRUE/FALSE）
  - `file_sha256`：该文件原文的 SHA-256

约束：`interpretation` 必须单独标明，不能当法规正文；`is_archived=TRUE` 的文件只能用于了解历史，不能作为当前建议的主要依据。

### 3. `regulation_chunks.jsonl`

用途：供检索程序查找的法规片段，按条款结构切分，不能只按固定字符数切断条款。

- 格式：JSON Lines，每行一个 JSON 对象，UTF-8
- 每行字段（对应方案 10.2 表9）：
  - `chunk_id`：片段唯一编号
  - `document_id`：所属文档编号
  - `standard_number`：OSHA 标准编号，如 `1910.269(a)(1)`
  - `section`：条款编号或章节位置
  - `title`：条款标题
  - `text`：官方原文
  - `source_type`：来源类型，同 inventory
  - `source_url`：官方原文链接
  - `effective_date`：生效日期
  - `retrieved_at`：获取日期
  - `is_archived`：是否归档
  - `risk_categories`：可选，该片段对应的 R1—R9（便于按风险类别检索）

约束：正式测试前知识库必须冻结；运行时只读已锁定的知识库，不能临时联网补材料。

### 4. `knowledge_manifest.json`

用途：记录知识库包含哪些文件、每个文件的校验值，保证版本可追溯。

- 格式：JSON，UTF-8
- 字段：
  - `knowledge_version`：知识库版本号
  - `generated_at`：生成时间
  - `schema_version`：本清单的格式版本
  - `documents`：数组，每项含 `document_id`、`file_name`、`sha256`
  - `chunks`：数组，每项含 `file_name`、`sha256`、`n_chunks`

### 5. `standard_document_mapping.csv`

用途：标准编号到官方文档片段的固定对应表，Retrieval 和 Audit 都要用它核对引用。

- 格式：CSV，UTF-8，首行为表头
- 表头：
  - `standard_number`：OSHA 标准编号
  - `document_id`：对应文档编号
  - `section`：对应条款位置

### 6. `retrieval_gold.csv`

用途：人工核对过的"查询问题 → 正确法规"答案，用来验证法规检索准不准（阶段6 验收）。

- 格式：CSV，UTF-8，首行为表头
- 表头：
  - `query_id`：查询编号
  - `query_text`：查询问题（中文即可）
  - `standard_numbers`：查询涉及的标准编号，多个用分号分隔
  - `risk_categories`：涉及的风险类别，多个用分号分隔
  - `gold_document_ids`：正确答案的文档编号，多个用分号分隔
  - `gold_sections`：正确条款位置，多个用分号分隔
  - `verified_by`：核对人
  - `verified_at`：核对日期

### 7. `retrieval_validation_metrics.csv`

用途：不同检索方法在验证集上的比较结果，用来在阶段6 锁定检索方法和返回条数。

- 格式：CSV，UTF-8，首行为表头
- 表头：
  - `method`：`keyword` / `vector` / `hybrid`
  - `top_k`：每次返回条数
  - `n_queries`：验证集查询数
  - `recall_at_5`：Recall@5
  - `mrr`：平均倒数排名
  - `ndcg_at_10`：nDCG@10
  - `eval_split`：验证集标记
  - `config_hash`：检索配置的 SHA-256
  - `created_at`：生成日期

---

## 三、学生2 要收集哪些法规来源

按方案 10.1，知识库优先收录：

1. OSHA 29 CFR 标准正文；
2. eCFR 对应现行法规；
3. OSHA Letters of Interpretation（解释文件，单独标 `interpretation`）；
4. OSHA 检查数据字段解释文件（Inspection Detail Definitions）；
5. OSHA 现场工作手册（Field Operations Manual）中与检查范围和优先级相关的章节。

电力相关重点标准：

- 1910.269：发电、输电和配电；
- 1910 的 S 分部分：一般电气安全；
- 1910.147：危险能量控制；
- 1910.132—1910.138：个人防护装备；
- 1926 的 V 分部分：电力输配电施工；
- 1926 的 M 分部分：坠落防护；
- 1926 的 X 分部分：梯子和楼梯；
- 1926 的 CC 分部分：起重设备。

---

## 四、交接时必须满足的硬性约束

- 知识库必须是固定版本，每份文件记录 SHA-256；
- 按条款结构切分，不得只按固定字符数切割导致条款边界被破坏；
- 解释文件与法规正文分开标注，归档文件不得作为当前建议的主要依据；
- 正式测试时只读锁定知识库，不联网、不用搜索引擎摘要当依据；
- 中文用于正文和人工复核表，程序字段用英文（本清单已按此标注）。

---

## 五、需要学生4（你）拍板的事

这些是你在接入和使用学生2 交付物之前必须定的，不是学生2 的活：

1. **基础模型选型**：一个可本地部署的开放权重模型 + 一个在线接口模型，各选谁；阶段9 实验启动前定，开发期先定一个默认模型。
2. **"无依据陈述率"判定协议**：原子陈述怎么切分、由谁判定（规则 / 独立 LLM / 人工抽查如何分工），这是主指标，阶段9 前定。
3. **Profile Agent 用规则还是 LLM**：建议 v0 用规则（可复现、可审计），阶段8 前定。
4. **Review Agent 的模型接口**：OpenAI 兼容 / 本地 vLLM / 其他；API key 管理与输入匿名化流程。
5. **检索方法组合**：关键词（BM25）/ 向量 / 混合最终用哪个，以及每次返回条数 top_k；在阶段6 用 `retrieval_gold.csv` 验证后锁定。
6. **向量索引选型**：FAISS / Qdrant / Chroma 三选一（方案明确要求三选一）。
7. **嵌入模型选型**：向量检索需要 embedding 模型，方案未指定，需要你定。
8. **旧正式测试标签的衔接**：8月17日已开封的旧答案怎么处理，与新方案"只开一次"如何衔接（需与导师、学生1 确认）。
9. **运行环境锁定方式**：pip-tools / conda-lock 二选一，阶段1 收尾定。
10. **R1—R9 分类表版本**：以哪份文件为锁定版，学生2/3 共同确认，你在画像和实验使用前必须拿到 SHA。
