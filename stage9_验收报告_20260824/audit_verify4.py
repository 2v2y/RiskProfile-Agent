"""第四轮复核：独立验证学生1/学生3 08-24 回复交付中的全部声明。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
S1 = ROOT / r"origin_data\from_student1\回复学生4_20260824\回复学生4_20260824"
S3 = ROOT / r"origin_data\from_student3\student3\学生4需要的文件"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def read_auto(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in read_auto(p).splitlines() if l.strip()]


out: dict = {}

# ========== 学生1 ==========
supp_new = S1 / "profile_supplement_8fields.csv"
supp_backup = S1 / "profile_supplement_8fields_backup_20260824.csv"
verify_py = S1 / "final_verify.py"
r6csv = S1 / "风险分类_R6修正.csv"
wl = S1 / "agent_profile_whitelist.json"
mapping = S1 / "standard_to_r1r9_mapping.csv"

out["s1_hashes"] = {
    "profile_supplement_8fields.csv": sha256(supp_new),
    "backup": sha256(supp_backup),
    "final_verify.py": sha256(verify_py),
    "风险分类_R6修正.csv": sha256(r6csv),
    "whitelist": sha256(wl),
    "mapping": sha256(mapping),
}

# model_hash 全量检查
rows = list(csv.DictReader(io.StringIO(read_auto(supp_new))))
full = "562d7837e0d2bc6389e95607e543f9b07db5be1b1fd0ad05487a1151b5001603"
mh = Counter(r.get("model_hash", "") for r in rows)
sev_bad = sum(1 for r in rows if full not in r.get("score_evidence", ""))
perc = [float(r["risk_percentile"]) for r in rows]
hsc_empty = sum(1 for r in rows if not (r.get("historical_standard_codes") or "").strip())
out["s1_supplement"] = {
    "n": len(rows),
    "model_hash_distinct": dict(mh),
    "model_hash_all_64": all(len(k) == 64 for k in mh),
    "score_evidence_bad": sev_bad,
    "risk_percentile_min": min(perc),
    "risk_percentile_max": max(perc),
    "historical_standard_codes_empty": hsc_empty,
}

# final_verify.py 是否全量检查
code = read_auto(verify_py)
out["final_verify_iloc0_still"] = "iloc[0]" in code or ".iloc[0]" in code
out["final_verify_has_loop"] = ("all(" in code) or ("model_hash" in code and "for " in code)
out["final_verify_lines_mentioning_model_hash"] = [
    l.strip()[:120] for l in code.splitlines() if "model_hash" in l
][:10]

# R6修正.csv 与映射复现
rc_rows = list(csv.DictReader(io.StringIO(read_auto(r6csv))))
out["r6csv"] = {
    "n_rows": len(rc_rows),
    "n_cols": len(rc_rows[0]),
    "header": list(rc_rows[0].keys()),
}
r6_final = [r for r in rc_rows if (r.get("最终主类") or "").strip() == "R6"]
out["r6csv_final_r6_rows"] = len(r6_final)
out["r6csv_final_r6_unique_std"] = len({(r.get("standard规范值") or "").strip() for r in r6_final})
out["r6csv_final_dist"] = dict(Counter((r.get("最终主类") or "").strip() for r in rc_rows))

# 尝试复现映射：按 standard规范值 去重取最终主类
mp_rows = list(csv.DictReader(io.StringIO(read_auto(mapping))))
mp_by_std: dict[str, tuple[str, str]] = {}
for r in mp_rows:
    mp_by_std.setdefault((r["standard_normalized"].strip(), r["standard_code"].strip()), (r["r_category"], r["r_category_name"]))
repro = {}
for r in rc_rows:
    std = (r.get("standard规范值") or "").strip()
    cat = (r.get("最终主类") or "").strip()
    if not std or not cat:
        continue
    repro.setdefault(std, cat)
matched = 0
mp_std_set = {k[0] for k in mp_by_std}
repro_set = set(repro)
for std in mp_std_set:
    if repro.get(std) == mp_by_std.get((std, std), (None, None))[0]:
        matched += 1
    elif repro.get(std):
        matched += 1
out["mapping_repro"] = {
    "mapping_rows": len(mp_rows),
    "mapping_unique_normalized": len(mp_std_set),
    "r6csv_unique_normalized": len(repro_set),
    "mapping_std_not_in_r6csv": sorted(mp_std_set - repro_set)[:10],
    "r6csv_std_not_in_mapping": len(repro_set - mp_std_set),
}
# R6 具体核对：mapping R6 标准 vs R6修正 R6 标准
mp_r6_std = {k[0] for k, v in mp_by_std.items() if v[0] == "R6"}
rc_r6_std = {(r.get("standard规范值") or "").strip() for r in r6_final}
out["r6_mapping_vs_r6csv"] = {
    "mapping_r6": len(mp_r6_std),
    "r6csv_r6": len(rc_r6_std),
    "mapping_r6_not_in_r6csv": sorted(mp_r6_std - rc_r6_std)[:10],
    "r6csv_r6_not_in_mapping": sorted(rc_r6_std - mp_r6_std)[:10],
}

# ========== 学生3 ==========
gold = S3 / "benchmark_gold_restricted.jsonl"
mani = S3 / "benchmark_manifest.json"
lookup = S3 / "case_8fields_lookup.jsonl"
blind = S3 / "human_blind_sample.csv"
supp3 = S3 / "profile_supplement_8fields.csv"
join_py = S3 / "p0_join_8fields_template.py"

out["s3_hashes"] = {
    "gold": sha256(gold),
    "manifest": sha256(mani),
    "lookup": sha256(lookup),
    "blind": sha256(blind),
    "supp": sha256(supp3),
    "join_py": sha256(join_py),
}
out["s3_supp_matches_s1"] = sha256(supp3) == sha256(supp_new)

grecs = load_jsonl(gold)
gkv = Counter()
nested_kv = Counter()
for r in grecs:
    gkv[str(r.get("knowledge_version"))] += 1
    for ref in r.get("gold_regulation_document_ids") or []:
        nested_kv[str(ref.get("knowledge_version"))] += 1
esdp = Counter(str(r.get("expected_safe_defer_or_pass")) for r in grecs)
out["gold_check"] = {
    "n": len(grecs),
    "top_knowledge_version": dict(gkv),
    "nested_knowledge_version": dict(nested_kv),
    "expected_safe_defer_or_pass": dict(esdp),
    "case_id_dup": len(grecs) - len({r["case_id"] for r in grecs}),
}

# gold 与旧 gold 差异
gold_old = ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl"
if gold_old.exists():
    old_recs = load_jsonl(gold_old)
    diff_fields = Counter()
    for a, b in zip(old_recs, grecs):
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                diff_fields[k] += 1
    out["gold_old_vs_new_diff_fields"] = dict(diff_fields)

# manifest 解析
manifest = json.loads(read_auto(mani))
out["manifest_version"] = manifest.get("version")
out["manifest_status"] = manifest.get("status")
of = manifest.get("output_files", {})
out["manifest_output_files"] = of
out["manifest_p0_resolution"] = manifest.get("p0_resolution")

# 比对 manifest 声明的 SHA 与工作区实际文件
declared = {}
for name, meta in of.items():
    if isinstance(meta, dict) and meta.get("sha256"):
        declared[name] = meta["sha256"]
out["manifest_declared_sha"] = declared
actual_cases = {}
for p in ROOT.rglob("benchmark_cases.jsonl"):
    actual_cases[str(p)] = sha256(p)
for p in ROOT.rglob("red_team_cases.jsonl"):
    actual_cases[str(p)] = sha256(p)
out["actual_case_file_hashes"] = actual_cases

# 查找 manifest 声明 SHA 是否匹配任何实际文件
all_hashes = {v: k for k, v in actual_cases.items()}
out["manifest_case_sha_matched"] = {
    name: (all_hashes.get(sha) if sha in all_hashes else None)
    for name, sha in declared.items()
    if name in ("benchmark_cases.jsonl", "red_team_cases.jsonl", "benchmark_gold_restricted.jsonl")
}

# lookup 校验
lrecs = load_jsonl(lookup)
bench_old = load_jsonl(ROOT / r"origin_data\from_student3\benchmark_cases.jsonl")
rt_old = load_jsonl(ROOT / r"origin_data\from_student3\red_team_cases.jsonl")
bench_keys = {(str(r.get("sample_id")), str(r.get("quarter"))) for r in bench_old}
rt_keys = {(str(r.get("sample_id")), str(r.get("quarter"))) for r in rt_old}
lk_keys = {(str(r.get("sample_id")), str(r.get("quarter"))) for r in lrecs}
lk_keys_list = [(str(r.get("sample_id")), str(r.get("quarter"))) for r in lrecs]
lk_dup = len(lk_keys_list) - len(set(lk_keys_list))
lk_case_types = Counter(str(r.get("case_type")) for r in lrecs)
lk_keyset = set()
for r in lrecs:
    lk_keyset.update(r.keys())
out["lookup_check"] = {
    "n": len(lrecs),
    "dup_keys": lk_dup,
    "keys": sorted(lk_keyset),
    "case_type": dict(lk_case_types),
    "bench_covered": len(lk_keys & bench_keys),
    "red_covered": len(lk_keys & rt_keys),
    "first": lrecs[0] if lrecs else None,
}

# lookup 字段覆盖率
fields = ["historical_standard_codes", "historical_risk_categories", "risk_category_counts",
          "risk_category_unmapped_rate", "risk_score", "risk_percentile", "model_version", "score_evidence"]
cov = {}
for f in fields:
    empty = sum(1 for r in lrecs if not r.get(f))
    cov[f] = {"present": len(lrecs) - empty, "empty": empty}
out["lookup_field_coverage"] = cov

# blind 文件
blind_rows = list(csv.DictReader(io.StringIO(read_auto(blind))))
out["blind_check"] = {
    "n": len(blind_rows),
    "header": list(blind_rows[0].keys()) if blind_rows else [],
    "encoding": "utf8" if True else None,
    "first": blind_rows[0] if blind_rows else None,
}

# red_team 泄漏字段复查（s3 与 s2）
for label, p in (
    ("rt_s3", ROOT / r"origin_data\from_student3\red_team_cases.jsonl"),
    ("rt_s2", ROOT / r"origin_data\from_student2\red_team_cases.jsonl"),
):
    recs = load_jsonl(p)
    leak = 0
    leak_types = Counter()
    for r in recs:
        blob = json.dumps(r, ensure_ascii=False)
        if "_leaked_label" in blob or "_leaked_entity_proxy_id" in blob:
            leak += 1
            leak_types[str(r.get("red_team_type"))] += 1
    out[label + "_leak"] = {"records_with_leak_fields": leak, "types": dict(leak_types)}

# p0 join 脚本内容
out["join_script"] = read_auto(join_py)[:4000]

REP = ROOT / "stage9_验收报告_20260824"
(REP / "audit_findings4.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK")
