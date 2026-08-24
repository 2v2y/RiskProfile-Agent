import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
d = json.load(open(r"G:\Projects\intern_project\CahngeDirect_eassy\stage9_验收报告_20260824\audit_findings.json", encoding="utf-8"))
h = d["hashes"]

OD = r"G:\Projects\intern_project\CahngeDirect_eassy\origin_data"
S9 = r"G:\Projects\intern_project\CahngeDirect_eassy\operate\stage9_edit"
GIT = r"G:\Projects\github_clone\eassy_electric"
REP = r"G:\Projects\intern_project\CahngeDirect_eassy\stage9_验收报告_20260824"


def short(p):
    p = p.replace(OD, "OD").replace(S9, "S9").replace(GIT, "GIT").replace(REP, "REP")
    return p


groups = {}
for p, v in h.items():
    if not v.get("exists"):
        print("MISSING:", short(p))
        continue
    key = p.split("\\")[-1]
    groups.setdefault(key, []).append((short(p), v["size"], round(v["mtime"]), v["sha256"]))

names = [
    "profiles_train_val.csv",
    "profile_supplement_8fields.csv",
    "standard_to_r1r9_mapping.csv",
    "agent_profile_whitelist.json",
    "regulation_chunks.jsonl",
    "standard_document_mapping.csv",
    "retrieval_gold.csv",
    "document_inventory.csv",
    "knowledge_manifest.json",
    "db_meta.json",
    "chunk_ids.json",
    "faiss_index.bin",
    "embeddings.npy",
    "benchmark_cases.jsonl",
    "red_team_cases.jsonl",
    "风险分类.csv",
    "frozen_model.joblib",
    "frozen_manifest.json",
    "feature_dictionary.csv",
    "画像汇总.csv",
    "画像复算.csv",
    "防泄漏审计.json",
    "文件交接.csv",
    "risk_score_verification.csv",
    "final_verify.py",
]
for name in names:
    if name not in groups:
        print("###", name, "(no hash entries)")
        continue
    print("###", name)
    for row in groups[name]:
        print("   ", row[0], "| size:", row[1], "| mtime:", row[2], "| sha:", row[3][:32])
