"""第三轮复核：确认“必须学生做”的每一项确实是缺失/不一致，排除字段名不同但内容等价的情况。"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(r"G:\Projects\intern_project\CahngeDirect_eassy")
GIT = Path(r"G:\Projects\github_clone\eassy_electric")


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

# ---------- 学生3 ----------
bench = load_jsonl(ROOT / r"origin_data\from_student3\benchmark_cases.jsonl")
bench_s2 = load_jsonl(ROOT / r"origin_data\from_student2\benchmark_cases.jsonl")
rt = load_jsonl(ROOT / r"origin_data\from_student3\red_team_cases.jsonl")
gold = load_jsonl(ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl")

# 1) 案例记录中是否有任何位置存在 8 个必需字段（递归搜索键名）
NEED = [
    "historical_standard_codes",
    "historical_risk_categories",
    "risk_category_counts",
    "risk_category_unmapped_rate",
    "risk_score",
    "risk_percentile",
    "model_version",
    "score_evidence",
]


def walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}" if path else k
            yield from walk_keys(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            yield from walk_keys(v, f"{path}[{i}]")


def any_key_contains(recs, needles):
    hits = set()
    for r in recs:
        for k in walk_keys(r):
            for n in needles:
                if n in k:
                    hits.add(k)
    return hits


out["bench_any_key_hits"] = sorted(any_key_contains(bench, NEED))
out["rt_any_key_hits"] = sorted(any_key_contains(rt, NEED))
out["gold_any_key_hits"] = sorted(any_key_contains(gold, NEED))

# gold 的 expected_profile_facts_subset 是否含风险/分数类字段
eps_keys = Counter()
for r in gold:
    for k in (r.get("expected_profile_facts_subset") or {}).keys():
        eps_keys[k] += 1
out["gold_expected_profile_facts_keys"] = dict(eps_keys)

# 2) 全量 diff：学生3版 vs 学生2版 cases，是否只有 knowledge_version 不同
diff_fields: Counter = Counter()
for a, b in zip(bench, bench_s2):
    for k in set(a) | set(b):
        if a.get(k) != b.get(k):
            diff_fields[k] += 1
out["bench_s3_vs_s2_diff_fields"] = dict(diff_fields)

# 3) profile_version 一致性
out["bench_profile_version"] = dict(Counter(str(r.get("profile_version")) for r in bench))
out["rt_profile_version"] = dict(Counter(str(r.get("profile_version")) for r in rt))
out["gold_profile_version"] = dict(Counter(str(r.get("profile_version")) for r in gold))
supp_header = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\data\02_train_validation\profile_supplement_8fields.csv"))))[0].keys()
out["supp_has_profile_version"] = "profile_version" in supp_header
prof3 = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"origin_data\from_student3\阶段4学生3交付给学生4\profiles_train_val.csv"))))
out["student3_profiles_profile_version"] = dict(Counter((r.get("profile_version") or "").strip() for r in prof3))

# 4) manifest 是否有文件级 SHA
mani = json.loads(read_auto(ROOT / r"origin_data\from_student3\benchmark_manifest.json"))
of = mani.get("output_files", {})
out["manifest_output_files"] = of
out["manifest_has_any_sha"] = any("sha" in str(v).lower() for v in of.values())

# 5) human_blind / 盲评 文件全盘搜索
out["blind_files"] = [
    str(p) for p in list(ROOT.rglob("*")) + list(GIT.rglob("*"))
    if p.is_file() and re.search(r"blind|盲评|human", p.name, re.I)
]

# 6) gold 是否在受限目录 / 有无 .gitignore 规则
out["gold_path_restricted"] = "restricted" in str(ROOT / r"origin_data\from_student3\benchmark_gold_restricted.jsonl").lower()
gitignores = []
for base in (ROOT, GIT):
    for gi in base.rglob(".gitignore"):
        txt = read_auto(gi)
        if "gold" in txt.lower() or "restricted" in txt.lower():
            gitignores.append({"file": str(gi), "matched_lines": [l for l in txt.splitlines() if "gold" in l.lower() or "restricted" in l.lower()][:5]})
out["gitignore_gold_rules"] = gitignores

# ---------- 学生1 ----------
# 7) model_hash 截断值是否为完整值前缀；score_evidence 是否全量完整
supp = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\data\02_train_validation\profile_supplement_8fields.csv"))))
full = "562d7837e0d2bc6389e95607e543f9b07db5be1b1fd0ad05487a1151b5001603"
trunc_vals = Counter()
sev_ok = 0
sev_bad = 0
for r in supp:
    mh = r.get("model_hash", "")
    if mh and mh != full:
        trunc_vals[mh] += 1
    sev = r.get("score_evidence", "")
    if full in sev:
        sev_ok += 1
    else:
        sev_bad += 1
out["model_hash_truncated_values"] = dict(trunc_vals)
out["model_hash_truncated_total"] = sum(trunc_vals.values())
out["score_evidence_full"] = sev_ok
out["score_evidence_bad"] = sev_bad

# 8) risk_percentile 是否有其他 0-1 字段/说明
conf_doc = read_auto(ROOT / r"origin_data\from_student1\学生4_确认包_20260824\学生4_确认包_20260824\学生4_确认文档_20260824.md")
out["confirm_doc_mentions_percentile"] = [l for l in conf_doc.splitlines() if "percentile" in l or "分位" in l]
out["supp_header"] = list(supp_header)

# 9) R6 映射来源核对：mapping R6 标准是否 ⊆ 风险分类复核主类 R6
mp = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\knowledge\standard_to_r1r9_mapping.csv"))))
rc = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\knowledge\风险分类.csv"))))
mp_r6 = {(r["standard_normalized"].strip(), r["standard_code"].strip()) for r in mp if r.get("r_category") == "R6"}
rc_rev_r6 = {(r.get("standard规范值") or "").strip() for r in rc if (r.get("复核主类") or "").strip() == "R6"}
rc_final_r6 = {(r.get("standard规范值") or "").strip() for r in rc if (r.get("最终主类") or "").strip() == "R6"}
mp_r6_std = {x[0] for x in mp_r6}
out["r6_mapping_count"] = len(mp_r6)
out["r6_review_count"] = len(rc_rev_r6)
out["r6_final_count"] = len(rc_final_r6)
out["r6_mapping_subset_of_review"] = mp_r6_std.issubset(rc_rev_r6)
out["r6_mapping_not_in_review"] = sorted(mp_r6_std - rc_rev_r6)[:10]
out["r6_review_not_in_mapping"] = len(rc_rev_r6 - mp_r6_std)

# 10) zip 内是否有 风险分类 相关文件（初分/复核/R6修正）
zip_hits = []
for z in ROOT.rglob("*.zip"):
    try:
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if "风险分类" in n or "R6" in n:
                    zip_hits.append((str(z), n))
    except Exception:
        pass
out["zip_risk_classification_entries"] = zip_hits

# ---------- 学生2 ----------
# 11) 新旧 manifest / db_meta 对比；chunks 一致性
man_old = json.loads(read_auto(ROOT / r"operate\stage9_edit\knowledge\knowledge_manifest.json"))
man_new = json.loads(read_auto(ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\knowledge_manifest.json"))
out["manifest_old"] = man_old
out["manifest_new_key_fields"] = {k: man_new.get(k) for k in ("知识库版本", "冻结日期", "总文档数", "总片段数", "向量数据库版本", "推荐检索方法", "推荐返回条数")}
db_old = json.loads(read_auto(ROOT / r"operate\stage9_edit\knowledge\vector_db\db_meta.json"))
db_new = json.loads(read_auto(ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\vector_db\db_meta.json"))
out["db_meta_old"] = db_old
out["db_meta_new"] = db_new
inv_old = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\knowledge\document_inventory.csv"))))
inv_new = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\document_inventory.csv"))))
out["inventory_old_rows"] = len(inv_old)
out["inventory_new_rows"] = len(inv_new)
inv_diff = 0
for a, b in zip(inv_old, inv_new):
    if a != b:
        inv_diff += 1
out["inventory_rows_differ"] = inv_diff

# 12) ndcg 字段全盘搜索
out["ndcg_files"] = [
    str(p) for p in ROOT.rglob("*")
    if p.is_file() and "ndcg" in p.name.lower()
]
metrics = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"stage9_验收报告_20260824\extracted_student2_20260824\交付_学生4\knowledge\retrieval_validation_metrics.csv"))))
out["metrics_ndcg_values"] = dict(Counter((r.get("ndcg_at_10") or "").strip() for r in metrics))

# 13) 解释资料片段映射核对
chunks = load_jsonl(ROOT / r"operate\stage9_edit\knowledge\chunks\regulation_chunks.jsonl")
map_rows = list(csv.DictReader(io.StringIO(read_auto(ROOT / r"operate\stage9_edit\knowledge\chunks\standard_document_mapping.csv"))))
map_by_chunk = {r.get("片段编号", ""): r for r in map_rows}
interp_chunks = [c for c in chunks if str(c.get("standard", "")).startswith("interpretation_")]
out["interp_chunks"] = [
    {
        "chunk_id": c["chunk_id"],
        "standard": c["standard"],
        "mapping_standard": map_by_chunk.get(c["chunk_id"], {}).get("OSHA标准编号", ""),
    }
    for c in interp_chunks[:6]
]
inv_keys = {r.get("文档编号", "") for r in inv_old}
out["interp_doc_id_in_inventory"] = sorted({c["standard"] for c in interp_chunks} & inv_keys)

# 14) 08-24 新文件是否仅存在于 zip
only_zip = []
for fname in ("retrieval_validation_metrics.csv", "knowledge_manifest.json", "document_inventory.csv", "db_meta.json"):
    locs = [str(p) for p in ROOT.rglob(fname) if p.is_file()]
    only_zip.append((fname, locs))
out["new_files_locations"] = only_zip

REP = ROOT / "stage9_验收报告_20260824"
(REP / "audit_findings3.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK")
