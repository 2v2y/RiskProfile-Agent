### 【P0】评价案例的输入卡缺少 Agent 必需字段 —— 这是阶段9目前最大的阻塞

#### 缺什么：benchmark_cases.jsonl（5337条）和 red_team_cases.jsonl（240条）里的 input_card.allowed_profile_facts 只有 21 个基础画像字段，缺以下 8 项：

    historical_standard_codes（历史OSHA标准编号）

    historical_risk_categories（历史风险类别 R1–R9）

    risk_category_counts（各风险类别出现次数）

    risk_category_unmapped_rate（未映射比例）

    risk_score（冻结风险分数）

    risk_percentile（季度内风险分位）

    model_version（模型版本）

    score_evidence（分数来源SHA）

**来源**：这 8 项字段学生1已经在 profile_supplement_8fields.csv（08-24确认包）里按 (sample_id, quarter) 逐条给全了（5337条全有值）。你当时的案例是从阶段4画像卡生成的，那张卡没有这几列，所以案例里就缺了。

**用在哪**：

Retrieval Agent 用 historical_standard_codes + historical_risk_categories 决定去法规库查哪些标准、按哪个风险类别查——缺了这俩，检索会直接返回“画像里没有标准编号”，全部空转；

Review/Audit 用 risk_score/risk_percentile/model_version/score_evidence 写“冻结风险”和溯源——缺了这些，建议卡的风险部分拿不到值，Audit 也无法核对分数来源。

**需要做什么（二选一）**：

方案A：重新生成案例集，把 allowed_profile_facts 按学生1确认版画像+补充表补齐这 8 个字段，red_team 同步补；补齐后 case_id/sample_id/quarter 必须仍与画像和 gold 100% 对齐，no_future_fields 校验保持 true；

方案B：正式实验改为直接读“画像+补充表”（当前程序就是这么跑的），不经过案例 input_card——可以，但需要你书面确认这个口径，并在 manifest 里写明。

影响：不解决的话，B1—B5 全部案例的检索都为空、全部转人工，方法对比和三个主指标全部失效。

### 2.【P1】版本号对齐：gold 还是 4.0，案例已经被改成 5.0-frozen

缺什么/不一致：你的 benchmark_gold_restricted.jsonl 全部 5337 条 knowledge_version=4.0（**你按照原来的标准统一的**）；学生2在 08-24 已经把 benchmark_cases.jsonl/red_team_cases.jsonl 改成 5.0-frozen；学生2新 manifest 也是 5.0-frozen。

来源：各人各自改版本号，没有三方对齐。

用在哪：论文里“知识库版本=5.0-frozen、gold=4.0”无法自洽，引用准确率、Recall 的版本声明不可信。

需要做什么：和你、学生2确认唯一冻结版本号（**建议统一 5.0-frozen**），然后 gold、cases、red_team、manifest 四个文件版本号全部改成同一个，重新给 SHA。





5.【P2】manifest 缺每个文件的 SHA-256

benchmark_manifest.json 的 output_files 只写了路径，没写 benchmark_cases.jsonl / benchmark_gold_restricted.jsonl / red_team_cases.jsonl 三个文件的 SHA-256。

用在哪：复现实验时无法确认用的就是同一批题目。

需要做什么：补上三个文件的 SHA-256（和交付的最终文件一致）。

6.【P2】human_blind_sample.csv 还没交

manifest 里写了 100 条分层盲评样本，但文件不在交付物里。这一步阻塞的是阶段10（人工盲评），暂不阻塞阶段9，请先给一个交付时间。

7.【P2】gold 口径需要写清楚（方法学说明）

gold 的法规引用是“行业组参考代表标准”，不是该单位真实违反的标准；R5/R6/R7/R9 四类代表标准在知识库里没有对应片段（evidence_available=false，共 21404 条引用）。

用在哪：引用准确率怎么算、缺失引用算不算错，都取决于这个口径。

需要做什么：在 manifest/文档里正式写明这个口径；评价程序会把“知识库没有该片段”单独统计，不按普通引用错误处理——请确认这个处理方式。

8.【P2】profile_version 统一

案例里写 FREEZE_20260814_001，程序加载画像时写的是 student1-profile-v1，两个值对不上。

需要做什么：统一成一个值（建议统一成冻结版 FREEZE_20260814_001）。