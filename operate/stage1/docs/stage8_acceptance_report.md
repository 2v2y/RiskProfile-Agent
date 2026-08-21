# 阶段8验收报告（RiskProfile-Agent）

记录日期：2026-08-21

## 一、阶段8完成情况

| 需求 | 实现文件 | 测试结果 |
| --- | --- | --- |
| 画像整理模块 | src/agents/profile_agent.py | PASS |
| 法规检索模块 | src/agents/retrieval_agent.py | PASS |
| 复核建议模块 | src/agents/review_agent.py | PASS |
| 内容审查模块（程序核对器） | src/agents/audit_agent.py | PASS |
| 内容审查模块（独立语义审查） | src/agents/semantic_audit_agent.py | PASS |
| 流程控制器（LangGraph 固定状态机） | src/orchestrator/graph.py | PASS |
| Agent Registry | configs/agent_registry.yaml | PASS |
| Prompt Registry（版本化） | configs/prompt_registry.yaml + configs/prompts/*.md | PASS |
| 禁止性表达规则 | configs/forbidden_claim_rules.yaml | PASS |
| 三类端到端样例 | tests/fixtures/e2e_normal.json / e2e_human_review.json / e2e_reject.json | PASS |
| 完整日志与证据追踪 | src/common/run_log.py（run_index.jsonl）+ orchestrator state_trace | PASS |
| 阶段8验收测试 | tests/test_stage8.py | 69/69 PASS |

## 二、修改了哪些文件

新增：

- configs/agent_registry.yaml
- configs/prompt_registry.yaml
- configs/forbidden_claim_rules.yaml
- configs/prompts/review_agent_v1.md
- configs/prompts/semantic_audit_v1.md
- src/agents/semantic_audit_agent.py
- src/orchestrator/__init__.py
- src/orchestrator/graph.py
- tests/fixtures/e2e_normal.json
- tests/fixtures/e2e_human_review.json
- tests/fixtures/e2e_reject.json
- tests/test_stage8.py
- docs/stage8_acceptance_report.md

修改：

- configs/config.json（增加 semantic_audit / orchestrator / prompts / registries / 检索方法说明）
- requirements.txt、requirements.lock（追加 langgraph / langchain-core / langchain-openai / pyyaml / jsonschema）
- src/agents/profile_agent.py（白名单严格校验 + 明确错误）
- src/agents/retrieval_agent.py（按阶段6冻结结论改为标准限定候选集 + TF-IDF 排序）
- src/agents/review_agent.py（证据不足转人工、Prompt 版本化）
- src/agents/audit_agent.py（ID一致性、标准编号溯源、规则文件外置）
- src/common/run_log.py（增加 run_index.jsonl 运行索引）

未修改：

- schemas/ 下四类 JSON Schema（已确认，保持冻结）
- origin_data/ 下学生1/2/3交付物
- tests/smoke_test.py（阶段1测试保持不动）
- src/pipeline/minimal_pipeline.py（阶段1顺序编排保留，供兼容与回归）
- knowledge/chunks/ 下学生2知识库五件套

## 三、测试结果

- Profile：PASS
- Retrieval：PASS
- Review：PASS
- Audit（程序核对器）：PASS
- Semantic Audit：PASS
- End-to-End 正常：PASS
- End-to-End 转人工：PASS
- End-to-End 拒绝：PASS
- Failure Close（证据为空）：PASS
- Max Audit Round：PASS
- Schema / Registry / 可复现性：PASS

汇总：

- tests/test_stage8.py：69/69 PASS
- tests/smoke_test.py：29/29 PASS（阶段1回归通过）

## 四、三个端到端案例

正常：最终状态 = PASS

转人工：最终状态 = HUMAN_REVIEW（建议卡 schema 值为 DEFER，二者同义：DEFER 即转人工）

拒绝：最终状态 = REJECT

说明：未修改已确认 Schema 的 final_verdict 枚举（PASS / DEFER / REJECT）。流程与报告中把 DEFER 标注为 HUMAN_REVIEW，是为了与方案“通过 / 转人工 / 拒绝”对齐。

## 五、目前仍然存在的问题

- TODO：Qwen 实机未连接。当前 provider=dummy，Review 与 Semantic Audit 走离线规则回退；服务器上把 configs/config.json 中 llm.provider 改为 qwen、配置 .env 后走 LLM。
- Mock：阶段8的三个端到端 REJECT / 最大轮次用例通过注入 Stub Review Agent 模拟“生成器输出非法内容”的真实失败路径；正常/转人工用例使用真实四模块。
- 未连接的真实服务：Qwen API、向量索引服务（当前 TF-IDF 为纯 Python/numpy 确定性实现，未接入 FAISS/Qdrant/Chroma）。
- 临时实现：Semantic Audit 在 dummy 下使用 independent_rule 回退，不是真正的独立 LLM 判断；需在 Qwen 环境验证 LLM 路径。
- 未验证部分：真实 profiles_train_val.csv 全量端到端跑批、B0—B5 实验指标、正式测试开封流程。

## 六、需要我确认的问题

1. 问题：final_verdict 是否在界面/论文中统一叫 HUMAN_REVIEW，还是保留 Schema 的 DEFER？
   影响：Schema 已冻结，不能改枚举；当前实现用 DEFER，报告层用 HUMAN_REVIEW 映射。
   当前缺少：UI/论文口径的最终决定。
   若确认 A（统一叫 HUMAN_REVIEW）：我在文档与后续 B 系列输出层做映射，Schema 仍为 DEFER。
   若确认 B（保留 DEFER）：后续全部按 DEFER 书写，不出现 HUMAN_REVIEW。

2. 问题：禁止性表达规则中，因果性/效果类表述是否纳入 claim 级自动 REJECT？
   影响：决定 Audit 的自动命中范围，影响 B 系列异常输入的拒绝对齐。
   当前缺少：研究方案 §14.5 只明确“违法认定/事故必然性/处罚意见”，因果与效果类在共同材料 §3 属于论文级边界。
   若确认 A：加入 forbidden_claim_rules.yaml claim_level_forbidden。
   若确认 B：保留在 paper_level_constraints，由人工复核把关。

3. 问题：正式打分使用学生1旧画像还是学生3新版画像，以及 jurisdiction_context 是否等价 context_site_state？
   影响：risk_score / risk_percentile / model_version / score_evidence 的最终回填与阶段5衔接。
   当前缺少：带 sklearn 的运行环境与字段等价性确认（见 stage5_blockers.md）。
   若确认 A（新版画像 + 字段等价）：在服务器安装 sklearn 后用 score_profiles.py 回填。
   若确认 B（旧版画像）：继续用学生1 frozen 产物，但需学生3确认字段口径。

## 七、下一步建议

P0：确认 Qwen 地址/Key/模型名并在服务器部署验证（不解决则 Review/Semantic 仍是离线规则，无法完成 B 系列 LLM 实验）。

P1：确认正式画像口径与 risk_score 回填方式（影响正常样例是否使用真实冻结分数）。

P1：确认 DEFER vs HUMAN_REVIEW 的对外口径（影响验收报告与论文表述一致性）。

P2：把 TF-IDF 与阶段6的 FAISS/Qdrant/Chroma 候选之一做对照，确认阶段9是否需要替换为正式向量索引。

P2：补充真实 profiles_train_val.csv 全量端到端跑批与证据账本覆盖率统计。
