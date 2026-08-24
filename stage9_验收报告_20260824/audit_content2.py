"""第二轮深度核验（只读）。"""

from __future__ import annotations

import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
REP = ROOT / "stage9_验收报告_20260824"


def read_auto(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def load_jsonl(p: Path) -> list[dict]:
    text = read_auto(p)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


out: dict = {}

# 1) frozen_manifest 内容与 model hash 一致性
fman = ROOT / r"origin_data\from_student1\student1\结果_验证\frozen_manifest.json"
if fman.exists():
    out["frozen_manifest"] = json.loads(read_auto(fman))

# 2) 确认包 mapping 与 R6修正 zip 内 mapping 是否一致（解压对比）
import zipfile

zip_mapping = None
zp = ROOT / r"origin_data\from_student1\发给学生4_R6修正.zip"
with zipfile.ZipFile(zp) as z:
    names = [n for n in z.namelist() if n.endswith("standard_to_r1r9_mapping.csv")]
    if names:
        zip_mapping = z.read(names[0])
confirmed_mapping = (ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\standard_to_r1r9_mapping.csv").read_bytes()
out["mapping_r6zip_vs_confirmed_same"] = zip_mapping == confirmed_mapping if zip_mapping is not None else None

# 3) gold 深度检查
gold = load_jsonl(ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl")
chunks = load_jsonl(ROOT / r"operate\stage9_edit\knowledge\chunks\regulation_chunks.jsonl")
chunk_ids = {c["chunk_id"] for c in chunks}
gold_kv = Counter(str(r.get("knowledge_version")) for r in gold)
gold_split = Counter(str(r.get("split")) for r in gold)
ref_chunks: Counter = Counter()
refs_missing = 0
refs_total = 0
gold_null_chunk = 0
gold_has_unclosed = Counter(str(r.get("gold_has_unclosed_component")) for r in gold)
gold_label = Counter(str(r.get("gold_label")) for r in gold)
for r in gold:
    for ref in r.get("gold_regulation_document_ids") or []:
        refs_total += 1
        cid = ref.get("chunk_id")
        if cid:
            ref_chunks[cid] += 1
            if cid not in chunk_ids:
                refs_missing += 1
        else:
            gold_null_chunk += 1
out["gold_depth"] = {
    "n": len(gold),
    "knowledge_version": dict(gold_kv),
    "split": dict(gold_split),
    "refs_total": refs_total,
    "refs_with_chunk_id": refs_total - gold_null_chunk,
    "refs_null_chunk": gold_null_chunk,
    "refs_chunk_not_in_kb": refs_missing,
    "gold_has_unclosed_component": dict(gold_has_unclosed),
    "gold_label": dict(gold_label),
    "ref_chunk_id_sample": list(ref_chunks)[:5],
}

# 4) benchmark_cases 与画像关联
bench = load_jsonl(ROOT / r"operate\stage9_edit\benchmark\automatic\benchmark_cases.jsonl")
prof = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\data\02_train_validation\profiles_train_val.csv"))))
prof_keys = {(r["sample_id"], r["quarter"]) for r in prof}
bench_keys = {(str(r.get("sample_id")), str(r.get("quarter"))) for r in bench}
bench_no_future = Counter(str(r.get("no_future_fields")) for r in bench)
bench_schema_ver = Counter(str(r.get("schema_version")) for r in bench)
bench_split = Counter(str(r.get("split")) for r in bench)
bench_strat_rc = Counter(str(r.get("stratification", {}).get("risk_category")) for r in bench)
bench_input_keys = Counter()
for r in bench:
    card = r.get("input_card") or {}
    for k in card.keys():
        bench_input_keys[k] += 1
out["bench_profiles_join"] = {
    "bench_n": len(bench),
    "profiles_n": len(prof),
    "bench_key_matched_to_profiles": len(bench_keys & prof_keys),
    "bench_key_unmatched": len(bench_keys - prof_keys),
    "no_future_fields": dict(bench_no_future),
    "schema_version": dict(bench_schema_ver),
    "split": dict(bench_split),
    "strat_risk_category": dict(bench_strat_rc),
    "input_card_keys": dict(bench_input_keys),
}

# 5) 画像字段值对比：学生1 vs 学生3 画像（同 sample_id 行差异）
prof1 = { (r["sample_id"], r["quarter"]): r for r in csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student1\student1\数据_分析数据\profiles_train_val.csv"))) }
prof3 = { (r["sample_id"], r["quarter"]): r for r in csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\profiles_train_val.csv"))) }
diff_rows = 0
diff_fields: Counter = Counter()
sample_diffs = []
common_cols = [c for c in list(prof1.values())[0].keys() if c in list(prof3.values())[0].keys()]
for k in prof1:
    if k not in prof3:
        continue
    a, b = prof1[k], prof3[k]
    d = [c for c in common_cols if a.get(c) != b.get(c)]
    if d:
        diff_rows += 1
        diff_fields.update(d)
        if len(sample_diffs) < 3:
            sample_diffs.append({"key": k, "fields": d[:8]})
out["s1_vs_s3_profiles"] = {
    "common_keys": len(set(prof1) & set(prof3)),
    "diff_rows": diff_rows,
    "diff_fields": dict(diff_fields),
    "samples": sample_diffs,
}

# 6) 风险分类.csv 最终主类分布
rc = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\knowledge\风险分类.csv"))))
final_dist = Counter((r.get("最终主类") or "").strip() for r in rc)
other_or_unmapped = Counter((r.get("是否Other或Unmapped") or "").strip() for r in rc)
out["risk_csv_final_dist"] = {
    "n": len(rc),
    "最终主类": dict(final_dist),
    "是否Other或Unmapped": dict(other_or_unmapped),
}

# 7) rag_retriever 版本对比（zip 提取 vs stage9）
rag_zip = REP / r"extracted_student2_20260824\交付_学生4\src\retrieval\rag_retriever.py"
rag_s9 = ROOT / r"operate\stage9_edit\src\retrieval\rag_retriever.py"
out["rag_retriever_same"] = rag_zip.read_bytes() == rag_s9.read_bytes()

# 8) 08-21 whitelist (from_student1 root) 内容关键差异
w21 = json.loads(read_auto(ROOT / r"origin_data\from_student1\agent_profile_whitelist.json"))
out["whitelist_0821_top"] = {k: w21.get(k) for k in ("version", "defined_by", "defined_date", "schema_version", "name") if k in w21}
out["whitelist_0821_has_r1_names"] = [k for k in w21.keys() if k not in ("version", "defined_by", "defined_date", "schema_version", "name")][:20]

# 9) red_team 知识库版本与结构
rt = load_jsonl(ROOT / r"origin_data\from_student3\red_team_cases.jsonl")
rt_types = Counter(str(r.get("red_team_type")) for r in rt)
rt_kv = Counter(str(r.get("knowledge_version")) for r in rt)
rt_outcome = Counter(str(r.get("expected_outcome")) for r in rt)
out["red_team_s3"] = {"n": len(rt), "types": dict(rt_types), "knowledge_version": dict(rt_kv), "expected_outcome": dict(rt_outcome)}

# 10) 时间切分审计 / 防泄漏审计 摘要
tl = json.loads(read_auto(ROOT / r"origin_data\from_student1\student1\结果_画像\防泄漏审计.json"))
out["leak_audit"] = tl
tsc = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student1\student1\结果_数据审计\时间切分审计.csv"))))
out["time_split_audit"] = {"n": len(tsc), "header": list(tsc[0].keys()), "rows": tsc[:8]}

# 11) 测试开封记录
openrec = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student1\student1\记录表\测试开封记录.csv"))))
out["test_open_record"] = {"n": len(openrec), "header": list(openrec[0].keys()), "rows": openrec[:5]}

# 12) stage1 contract_check 报告
cc = ROOT / r"operate\stage1\docs\contract_check_report.json"
if cc.exists():
    out["stage1_contract_check"] = json.loads(read_auto(cc))
kcc = ROOT / r"operate\stage1\docs\knowledge_contract_check_report.json"
if kcc.exists():
    out["stage1_knowledge_contract_check"] = json.loads(read_auto(kcc))

REP.joinpath("audit_findings2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK")
