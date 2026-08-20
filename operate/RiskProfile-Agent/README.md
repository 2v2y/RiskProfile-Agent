# RiskProfile-Agent（阶段1：环境与最小端到端样例）

本目录按《RiskProfile-Agent 研究方案》31.2 推荐的目录结构搭建，当前实现阶段1（31.4）。

## 阶段1目标
- 锁定 Python 依赖（`requirements.lock`）
- 四类格式规则：画像、法规证据、复核建议卡、内容审查结果
- 统一运行日志（JSONL）：模型、指令、输入输出、耗时、异常
- 用少量非正式（脱敏/占位）样例跑通 画像→检索→建议卡→审计 的最小链路
- 同一输入可重复产生格式正确的输出；程序报错不会覆盖已有实验输出

## 目录说明
- `configs/`：运行配置与 Agent 白名单（草稿版）
- `schemas/`：四类 JSON 格式规则（人可读契约）
- `src/`：模块代码（common / agents / pipeline）
- `tests/`：正常样例、失败样例与冒烟测试
- `knowledge/`：知识库占位（正式内容等学生2交付）
- `data/`、`benchmark/`、`runs/`、`manifests/`、`paper/`：按方案预留

## 运行冒烟测试
```powershell
python tests/smoke_test.py
```

## 当前已知缺口（详细见 docs/stage1_blockers.md）
- 画像卡缺新方案字段：R1—R9 风险类别、risk_score、risk_percentile、质量标记等（等学生3 v2）
- 知识库 5 件套未交付（等学生2）
- 正式测试输入与旧开封标签的衔接规则（需导师/学生1确认）
- 基础模型选型与"无依据陈述率"判定协议（需拍板）

## 模型接入分层

模型调用统一走 `src/llm/client.py`，分层为：Agent -> LLMClient -> LangChain -> Qwen 服务器。
Agent 只依赖 `client.generate(messages)`，不在内部写死模型调用。

- 阶段1 默认 `provider=dummy`，离线占位，不发网络请求；
- 服务器部署时把 `configs/config.json` 里 `llm.provider` 改为 `qwen`，并通过环境变量传入地址、key、模型名。

## 服务器部署步骤

```bash
git clone <repo-url>
cd RiskProfile-Agent

python3.11 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：填 QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL

# 把 configs/config.json 里 llm.provider 改为 "qwen"

python tests/smoke_test.py
```

## 本地与服务器协作约定

- 本地和服务器都统一用 Python 3.11；`requirements.txt` 是服务器依赖，`requirements.lock` 是本地快照。
- `.env` 不入库；密钥只走环境变量。
- `runs/`、`__pycache__/`、受限数据目录都在 `.gitignore` 里，不要强制上传。
- 实验输出每次写入独立 `runs/` 目录，带 SHA-256 清单，正式测试前冻结配置与知识库。

## 后续 Multi-Agent 扩展方向

- 阶段8 把 `ReviewAgent` 的 `use_llm` 打开，走 `llm_client` 接入 Qwen；
- 阶段8 把 `minimal_pipeline.py` 的顺序编排迁移到 LangGraph 固定状态机；
- 检索阶段6 用学生2的 `retrieval_gold.csv` 对比关键词/向量/混合，选定索引后接入 Retrieval Agent；
- 审计的独立语义审查在阶段8 追加，与确定性核对器分开，避免生成模型自己给自己打分。
