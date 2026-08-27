# profile_agent 规则说明 v1（2026-08-27，Stage9 自包含版）

Profile Agent 是**确定性规则模块（不使用 LLM）**，本文件是其可追溯的规则说明
（等价于该 Agent 的“Prompt”），供论文/评审引用，不参与运行时推理。

## 职责
1. 只读取白名单（`data/profiles/agent_profile_whitelist.json`）允许的画像字段；
2. 把画像卡转换为结构化原子事实；每个数字/日期/ID 关键事实记录来源
   `profile:<field_name>`；
3. 禁止补造管理情况、现场情况、违法判断、事故必然性和数据中不存在的事实；
4. 输入字段不符合白名单时返回明确错误（严格模式）或转人工。

## 事实生成规则
- 空值 / 空列表 / 空字典不生成事实；
- 数字字段按 `_STATEMENT_TEMPLATES` 生成中文陈述（如“截至该季度，历史共有 N 次成熟检查”）；
- `risk_percentile` 从 0–1 比例换算为百分位陈述；
- 画像元数据（sample_id / quarter / ranking_cutoff / profile_version / industry_group）
  不进入事实列表，只用于溯源。

## 输入 Schema
- `src/common/pydantic_schemas.py::ProfileCard`（与 `config/schema_mapping.json` 对应）。

## 输出
- `{"sample_id": str, "n_facts": int, "facts": [{fact_id, statement_zh, field, value, provenance}]}`
