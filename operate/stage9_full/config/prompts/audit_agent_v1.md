# audit_agent 规则说明 v1（2026-08-27，Stage9 自包含版）

Audit Agent（确定性程序核对器）是**确定性规则模块（不使用 LLM）**，本文件是其
可追溯的规则说明（等价于该 Agent 的“Prompt”），供论文/评审引用，不参与运行时推理。
独立的 LLM 语义审查由 `semantic_audit_agent_v1.md` 描述。

## 检查项（每条原子陈述）
1. `number_consistency`：数字与画像来源一致；样本 ID 一致；
2. `citation_exists`：法规引用必须真实存在于检索结果；陈述中的 OSHA 标准编号
   必须来自检索证据或画像输入；
3. `evidence_supports`：陈述必须有画像或法规证据引用；
4. `forbidden_claim`：禁止违法认定、处罚建议、事故必然性等（规则文件
   `config/forbidden_claim_rules.yaml`，FC-01…FC-08）；
5. `missing_info_hidden`：存在缺失标记时必须如实说明不确定性；
6. `future_info_used`：禁止使用未来字段或画像截止日之后的日期；
7. `regulation_mix`：禁止同一陈述混用 OSHA 与中国法规。

## 判定
- 硬性失败（number/citation/forbidden/future/regulation_mix）→ REJECT；
- 软性失败（evidence_supports/missing_info_hidden）→ DEFER；
- 全部通过 → PASS。

## 输出 Schema
- `src/common/pydantic_schemas.py::AuditResult`（逐 claim PASS/DEFER/REJECT + 原因）
