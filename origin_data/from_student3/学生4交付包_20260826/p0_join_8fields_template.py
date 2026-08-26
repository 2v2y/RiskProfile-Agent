"""
P0 (阶段9前确认-Item1) 关联脚本 —— 方案B 最终执行件
======================================================
用途：把学生1 交付的 profile_supplement_8fields.csv 与案例关联，
      生成 Agent 运行时要读取的 8 字段查表（不污染案例 input_card）。

输入（已就位）：
  1) 学生4需要的文件/profile_supplement_8fields.csv  —— 学生1 交付（来源：刘知桦819 目录下的 profile_supplement_8fields.csv）
  2) 阶段7/阶段7产物/benchmark_cases.jsonl            —— 5337 例
  3) 阶段7/阶段7产物/red_team_cases.jsonl             —— 240 例

关联键：sample_id, quarter
期望命中：bench 5337/5337；red_team 240/240

输出：学生4需要的文件/case_8fields_lookup.jsonl
      —— 每条记录：{sample_id, quarter, split, <8 字段>}
      —— 案例 input_card 保持纯净（allowed_profile_facts 仅 21 基础字段，no_future_fields=true），
         8 字段经此查表在运行时补入（符合 m2_model.used=false 立场）。

说明：
  - 仅提取 P0 所需的 8 字段 + 关联键 + split，不内嵌进案例卡。
  - historical_standard_codes 在源表中存在部分空值（安全锁残留），脚本如实保留空串并计数，
    不伪造、不回填。
  - 缺失关联键会在 stderr 报错并导出 unmatched_keys.csv 供排查。
"""
import csv, json, os, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
SUPP = os.path.join(BASE, "profile_supplement_8fields.csv")
BENCH = os.path.join(BASE, "..", "阶段7", "阶段7产物", "benchmark_cases.jsonl")
RED = os.path.join(BASE, "..", "阶段7", "阶段7产物", "red_team_cases.jsonl")
OUT = os.path.join(BASE, "case_8fields_lookup.jsonl")
UNMATCHED = os.path.join(BASE, "p0_unmatched_keys.csv")

EIGHT = ["historical_standard_codes", "historical_risk_categories",
         "risk_category_counts", "risk_category_unmapped_rate",
         "risk_score", "risk_percentile", "model_version", "score_evidence"]


def load_supplement(path):
    table = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["sample_id"], row["quarter"])
            rec = {k: row.get(k, "") for k in EIGHT}
            table[key] = rec
    return table


def case_keys(path):
    keys = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            sid = o.get("sample_id") or o.get("input_card", {}).get("sample_id")
            q = o.get("quarter") or o.get("input_card", {}).get("quarter")
            split = o.get("split") or o.get("input_card", {}).get("split") or ""
            if sid is not None and q is not None:
                keys.append((str(sid), str(q), str(split)))
    return keys


def main():
    supp = load_supplement(SUPP)
    print(f"补充表载入唯一键: {len(supp)}")

    bench = case_keys(BENCH)
    red = case_keys(RED)
    print(f"benchmark 案例键: {len(bench)} | red_team 案例键: {len(red)}")

    empty_count = {k: 0 for k in EIGHT}
    out_rows = []
    unmatched = []
    for src, keys in (("benchmark", bench), ("red_team", red)):
        for sid, q, split in keys:
            key = (sid, q)
            if key not in supp:
                unmatched.append({"source": src, "sample_id": sid, "quarter": q})
                continue
            rec = {"sample_id": sid, "quarter": q, "split": split}
            for k in EIGHT:
                v = supp[key][k]
                rec[k] = v
                if v is None or str(v).strip() == "":
                    empty_count[k] += 1
            out_rows.append(rec)

    if unmatched:
        with open(UNMATCHED, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["source", "sample_id", "quarter"])
            w.writeheader()
            w.writerows(unmatched)
        raise SystemExit(f"ERROR: {len(unmatched)} 个案例键在补充表中无匹配，已导出 {UNMATCHED}")

    with open(OUT, "w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # LF 归一后算 SHA
    with open(OUT, "rb") as f:
        raw = f.read()
    lf = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    sha = hashlib.sha256(lf).hexdigest()
    with open(OUT, "wb") as f:
        f.write(lf)

    print(f"已写出查表: {OUT}")
    print(f"查表记录数: {len(out_rows)} (bench {len(bench)} + red {len(red)})")
    print(f"查表 SHA-256 (LF): {sha}")
    print("--- 各字段空值统计（仅计数）---")
    for k in EIGHT:
        print(f"  {k}: 空值 {empty_count[k]} / {len(out_rows)}")


if __name__ == "__main__":
    main()
