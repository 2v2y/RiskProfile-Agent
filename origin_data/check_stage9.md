已修复并验证（离线 smoke 仍通过，且现在 `provider=qwen` 时 B1–B4 会真正调 Qwen）。下面是服务器上跑 `stage9_full` 的完整操作，包括从 GitHub 同步。

## 0. 先确认两件前提

- 服务器上有本仓库（如果没有，先 clone，见下）；
- 服务器上 `python` 在 PATH，且阶段八依赖已装（pydantic/numpy/langgraph 等）；学生2检索器还需要 `faiss-cpu sentence-transformers torch`。

SSH 登录、conda 环境（`conda activate test`）、启动 Qwen 的具体命令见项目里的 [qwen_operate.md](G:/Projects/intern_project/CahngeDirect_eassy/origin_data/qwen_operate.md)，下面不再重复账号和启动模型的细节。

## 1. 从 GitHub 同步代码

仓库地址：`https://github.com/2v2y/RiskProfile-Agent.git`，分支 `main`。

首次（服务器还没有仓库）：

```bash
cd ~
git clone https://github.com/2v2y/RiskProfile-Agent.git
cd RiskProfile-Agent
```

以后更新（服务器已有仓库，拉最新）：

```bash
cd ~/RiskProfile-Agent
git fetch origin
git reset --hard origin/main      # 让本地代码与远端完全一致
git clean -fd                      # 删掉本地未被跟踪的杂文件（不含 .gitignore 项）
```

`stage9_full/results/` 和 `.env` 都在 `.gitignore` 里，所以 `git pull/reset` 不会删你的实验结果和密钥配置，可以放心拉。

## 2. 准备数据

`stage9_full` 不把数据写死在代码里，通过 `config/experiment_config.json` 的 `data.*` 相对路径引用仓库内的 `origin_data/`。两种情形：

- 如果 `origin_data/` 已经随仓库提交：`git pull` 就会带上，无需额外操作；
- 如果服务器上的数据放在别处（或不想把受限数据放仓库）：把已验收数据放到服务器某个目录，保持同样的子目录结构，然后用环境变量指向它：

```bash
export RP_DATA_ROOT=/data/riskprofile
```

数据根下要有的子路径见 [data/README.md](G:/Projects/intern_project/CahngeDirect_eassy/operate/stage9_full/data/README.md)。

## 3. 装依赖、配环境

```bash
cd ~/RiskProfile-Agent/operate/stage9_full
python -m pip install -r requirements.txt
python -m pip install faiss-cpu sentence-transformers torch   # 学生2 FAISS/BGE 检索器
cp .env.example .env
```

编辑 `.env`（关键三项，务必与 vLLM 服务名一致）：

```bash
QWEN_BASE_URL=http://127.0.0.1:8000/v1
QWEN_MODEL=/DATA/models/Qwen3.8-27B
QWEN_API_KEY=EMPTY
```

BGE 模型 `BAAI/bge-small-en-v1.5` 首次会自动下载；服务器离线就提前缓存，或用 `TRANSFORMERS_CACHE` 指到缓存目录（见学生2接入指南）。
## 验证
给你的验证办法（先跑这一条，再跑实验）
我加了一个联通自检脚本 [check_qwen.py](G:/Projects/intern_project/CahngeDirect_eassy/operate/stage9_full/experiments/check_qwen.py)，服务器上先跑：
cd ~/RiskProfile-Agent/operate/stage9_full
cp .env.example .env        # 填好 QWEN_BASE_URL / QWEN_MODEL
python -m experiments.check_qwen
它会打印 provider/环境变量、真的调一次 Qwen、并尝试把返回解析成 review_points。这一步 PASS 之前，我不能说确定能真实调 Qwen。过了之后，再按顺序跑：
python -m experiments.run_smoke --n 3
python -m experiments.run_comparison --split validation --limit 10   # 先小规模，确认 B1-B5 都出真实内容


## 4. 用真实 Qwen 跑正式实验（关键开关）

离线默认是 dummy（假模型，只验证框架）。正式实验要打开真实 LLM：

编辑 `config/experiment_config.json`：

```json
"llm": { "provider": "qwen", ... },
"review": { "use_llm": true, ... },
"semantic_audit": { "use_llm": true, ... }
```

确认 Qwen 服务已起（在服务器上）：

```bash
curl http://127.0.0.1:8000/v1/models
```

有 JSON 返回 = 服务在跑；否则先按 qwen_operate.md 启动 vLLM。

## 5. 跑实验（按顺序）

先 smoke，确认整条链路在服务器上通：

```bash
cd ~/RiskProfile-Agent/operate/stage9_full
python -m experiments.run_smoke --n 3
```

再跑正式对比（建议先小规模验证，再全量）：

```bash
python -m experiments.run_comparison --split validation --limit 50    # 先小规模
python -m experiments.run_comparison --split validation                # 全量 681 个验证样本
python -m experiments.run_ablation --split validation
python -m experiments.run_error_analysis --split validation
```

正式 Test 见 [run_test.py](G:/Projects/intern_project/CahngeDirect_eassy/operate/stage9_full/experiments/run_test.py) 与 README（Test 数据封存、不入库，需 `--confirm-test` + 封存清单）。

## 6. 看结果

跑完按 [查看实验结果.md](G:/Projects/intern_project/CahngeDirect_eassy/operate/stage9_full/查看实验结果.md) 查看：对比看 `results/comparison/*/summary.csv`，消融看 `ablation_summary.csv`，错误看 `error_analysis.csv`，单样本深挖看 B5 目录里的 `review_card.json`/`audit.json`/`retrieval.json`。

## 7. 提交结果回 GitHub 的注意点

`results/` 是 gitignored，实验输出不会进仓库；如果要把某次结果归档，放到专门的受控位置（不要提交受限数据/密钥）。

---

补充两点我这边刚做的调整，跟“服务器真实运行”直接相关，都还没提交到 Git：

1. 修了 `baselines/base.py`：之前 B1–B4 把 LLM 写死成离线假模型，服务器上即使 `provider=qwen` 也不会真调 Qwen；现在改成 `provider=qwen` 时走真实 Qwen、dummy 时才用假模型。
2. 上一轮做的 [查看实验结果.md](G:/Projects/intern_project/CahngeDirect_eassy/operate/stage9_full/查看实验结果.md) 也还没提交。

要我把这两处一起 `git add` + `commit`（推送仍需你有网络的环境执行 `git push origin main`）吗？