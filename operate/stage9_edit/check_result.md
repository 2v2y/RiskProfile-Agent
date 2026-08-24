# 阶段9运行结果查看说明（check_result）

阶段9跑完后，输出分两部分：统一干跑结果目录，以及 B5 单独产生的详细运行目录。

## 一、统一干跑结果目录

`run_baselines.py` 会生成：

```text
runs/20260823_233347_stage9_dryrun_xxxx/
├── config.json
├── case_0000.json
├── case_0001.json
├── run_log.jsonl
└── output_manifest.json
```

其中：

- `case_0000.json`：第 1 张画像在 B0—B5 六个方法下的完整输出；
- `run_log.jsonl`：每张画像、每个方法的运行记录，包括耗时、模型、最终判定；
- `output_manifest.json`：所有 `case_*.json` 的 SHA-256 清单。

每个 `case_*.json` 里，每个方法包含：

```text
method
sample_id / quarter
final_verdict
profile_facts
retrieval
draft_review
audit
semantic_audit
latency_ms
input_chars / output_chars
model
```

B5 还会额外带一个 `run_dir`，指向它单独生成的详细目录。

## 二、B5 单独产生的详细目录

B5 走完整 LangGraph 流程，会为每张画像再生成：

```text
runs/20260823_xxxxxx_stage9_b5_xxxx/
├── profile_facts.json
├── retrieval.json
├── draft_review.json
├── audit.json
├── semantic_audit.json
├── review_card.json
├── state_trace.json
├── run_log.jsonl
└── output_manifest.json
```

这里能看到 B5 最终建议卡、审计结果、独立语义审查和每一步状态变化。

## 三、在服务器上查看结果

进入阶段9目录：

```bash
cd ~/RiskProfile-Agent/operate/stage9_edit
```

查看最近的运行目录：

```bash
ls -dt runs/*/ | head
```

查看六个方法的最终判定和耗时：

```bash
python -c "import json,pathlib; d=sorted([p for p in pathlib.Path('runs').glob('*stage9_dryrun*') if p.is_dir()], key=lambda p:p.stat().st_mtime)[-1]; print('run_dir:', d); [print(f.name, '->', {m:r.get('final_verdict') for m,r in json.loads(f.read_text(encoding='utf-8')).items()}) for f in sorted(d.glob('case_*.json'))]"
```

查看运行日志：

```bash
latest=$(ls -dt runs/*stage9_dryrun* | head -n 1)
cat "$latest/run_log.jsonl"
```

查看 B5 最终建议卡：

```bash
python -c "import json,pathlib; d=sorted([p for p in pathlib.Path('runs').glob('*stage9_b5*') if p.is_dir()], key=lambda p:p.stat().st_mtime)[-1]; card=json.loads((d/'review_card.json').read_text(encoding='utf-8')); print('dir:',d); print('final_verdict:',card['final_verdict']); print('review_points:',[p['focus_zh'] for p in card['review_points']])"
```

查看 B5 审查被拒绝或转人工的原因：

```bash
latest=$(ls -dt runs/*stage9_b5* | head -n 1)
cat "$latest/audit.json"
cat "$latest/semantic_audit.json"
```

## 四、一句话总结

- `runs/*stage9_dryrun*`：看 B0—B5 六种方法的对比总表；
- `runs/*stage9_b5*`：看 B5 的完整证据链和最终建议卡。
