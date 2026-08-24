#!/usr/bin/env python3
"""
最终综合验证脚本
检查项: M2分数复算 + 文件SHA-256 + R1-R9一致性 + model_hash + 字段映射 + manifest一致性

使用方法: cd /home/liu/osha && .venv/bin/python3 /mnt/c/Users/LENOVO/Desktop/小刘skill/final_verify.py
"""
import sys
import json
import hashlib
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path("/home/liu/osha")
WIN = Path("/mnt/c/Users/LENOVO/Desktop/小刘skill")
MODEL_PATH = ROOT / "结果/03_验证/frozen_model.joblib"
PROFILES_PATH = ROOT / "数据/02_分析数据/profiles_train_val.csv"
MANIFEST_PATH = ROOT / "结果/03_验证/frozen_manifest.json"
SUPPLEMENT_PATH = WIN / "profile_supplement_8fields.csv"
WHITELIST_PATH = WIN / "agent_profile_whitelist.json"
MAPPING_PATH = WIN / "standard_to_r1r9_mapping.csv"

results = []
def check(name, status, detail=""):
    results.append((name, status, detail))
    tag = "PASS" if status else "FAIL"
    print(f"  [{tag}] {name}: {detail}")

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest().upper()

print("=" * 70)
print("最终综合验证")
print("=" * 70)

# ============================================================
# 1. M2 分数复算
# ============================================================
print("\n[1] M2 分数复算验证")
bundle = joblib.load(MODEL_PATH)
m2_pipeline = bundle["models"]["M2"]
profiles = pd.read_csv(PROFILES_PATH, encoding="utf-8")
if "jurisdiction_context" in profiles.columns:
    profiles = profiles.rename(columns={"jurisdiction_context": "context_site_state"})
scores = m2_pipeline.predict_proba(profiles)[:, 1]
profiles["recalc"] = scores

supplement = pd.read_csv(SUPPLEMENT_PATH, encoding="utf-8")
merged = profiles[["sample_id", "recalc"]].merge(
    supplement[["sample_id", "risk_score"]], on="sample_id", how="inner"
)
merged["diff"] = abs(merged["recalc"] - merged["risk_score"])
max_diff = merged["diff"].max()
match_pct = (merged["diff"] < 1e-6).sum() / len(merged) * 100
check("M2分数复算", max_diff < 1e-4,
      f"{len(merged)}行, 精确匹配{match_pct:.1f}%, 最大差异{max_diff:.2e}")

# ============================================================
# 2. 文件 SHA-256
# ============================================================
print("\n[2] 文件 SHA-256")
for name, path in [
    ("standard_to_r1r9_mapping.csv", MAPPING_PATH),
    ("agent_profile_whitelist.json", WHITELIST_PATH),
    ("profile_supplement_8fields.csv", SUPPLEMENT_PATH),
    ("frozen_model.joblib", MODEL_PATH),
]:
    h = sha256_file(path)
    print(f"  {name}: {h}")

# ============================================================
# 3. R1-R9 一致性
# ============================================================
print("\n[3] R1-R9 一致性")
mapping_df = pd.read_csv(MAPPING_PATH, encoding="utf-8")
with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
    whitelist = json.load(f)

# 从 mapping.csv 提取 R1-R9 名称
mapping_names = {}
for _, row in mapping_df.iterrows():
    cat = row["r_category"]
    name = row["r_category_name"]
    if cat not in mapping_names:
        mapping_names[cat] = name

# 从 whitelist 提取 R1-R9 名称 (从 KEY 提取，不是 VALUE)
wl_cats = whitelist.get("r1_r9_categories", {})
wl_names = {}
for key in wl_cats:
    if key.startswith("R") and "_" in key:
        rcode = key.split("_")[0]
        wl_names[rcode] = key[len(rcode)+1:]  # 去掉 "R1_" 前缀

# 比较
r_codes = sorted(set(mapping_names.keys()) | set(wl_names.keys()))
r9_consistent = True
for rc in r_codes:
    m_name = mapping_names.get(rc, "MISSING")
    w_name = wl_names.get(rc, "MISSING")
    m_norm = m_name.replace(" ", "").replace("、", "")
    w_norm = w_name.replace(" ", "").replace("、", "").replace(rc + "_", "")
    if m_norm != w_norm:
        r9_consistent = False
        print(f"  MISMATCH {rc}: mapping={m_name} vs whitelist={w_name}")
check("R1-R9三文件一致", r9_consistent,
      f"{len(r_codes)}个类别全部匹配" if r9_consistent else "存在不一致")

# ============================================================
# 4. model_hash 完整性（全量5337行）
# ============================================================
print("\n[4] model_hash 完整性（全量5337行）")
# 检查所有行的 model_hash 长度
hash_lengths = supplement["model_hash"].astype(str).str.len()
all_64 = (hash_lengths == 64).all()
non_64_count = (hash_lengths != 64).sum()
check("model_hash全部为64位", all_64,
      f"全量{len(supplement)}行: 64位={int((hash_lengths==64).sum())}, 非64位={int(non_64_count)}")

# 检查所有行的 model_hash 值是否一致
hash_unique = supplement["model_hash"].astype(str).unique()
all_same = len(hash_unique) == 1
check("model_hash全部一致", all_same,
      f"唯一值数量={len(hash_unique)}, 值={str(hash_unique[0])[:16]}..." if all_same else f"不一致: {len(hash_unique)}个不同值")

sample_hash = str(hash_unique[0]) if all_same else str(supplement["model_hash"].iloc[0])

# 与 manifest 对比
with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
    manifest = json.load(f)
frozen_model_entry = manifest.get("files", {}).get("frozen_model", {})
manifest_hash = frozen_model_entry.get("sha256", "").lower()
supplement_hash = sample_hash.lower()
check("model_hash与manifest一致", manifest_hash == supplement_hash,
      f"manifest={manifest_hash[:16]}... supplement={supplement_hash[:16]}...")

# 与实际文件 SHA-256 对比
actual_model_hash = sha256_file(MODEL_PATH).lower()
check("model_hash与实际文件一致", actual_model_hash == supplement_hash,
      f"文件={actual_model_hash[:16]}... supplement={supplement_hash[:16]}...")

# score_evidence 全量检查：所有行都包含完整hash
evidence_contains = supplement["score_evidence"].astype(str).str.lower().str.contains(supplement_hash, na=False)
all_evidence_ok = evidence_contains.all()
evidence_fail_count = int((~evidence_contains).sum())
check("score_evidence全部包含完整hash", all_evidence_ok,
      f"全量{len(supplement)}行: 包含={int(evidence_contains.sum())}, 缺失={evidence_fail_count}")

# ============================================================
# 5. 字段映射
# ============================================================
print("\n[5] 字段映射")
fm = whitelist.get("field_name_mapping", {})
mappings = fm.get("mappings", {})
expected_mappings = {
    "candidate_naics_group": "industry_group",
    "cutoff_date": "ranking_cutoff"
}
mapping_ok = True
for src, dst in expected_mappings.items():
    actual = mappings.get(src)
    if actual != dst:
        mapping_ok = False
        print(f"  MISMATCH: {src} -> {actual} (expected {dst})")
check("字段映射完整", mapping_ok,
      f"{len(mappings)}个映射: {mappings}")

# ============================================================
# 6. frozen_model Pipeline 结构
# ============================================================
print("\n[6] Pipeline 结构")
steps = [(name, type(step).__name__) for name, step in m2_pipeline.steps]
print(f"  Steps: {steps}")
has_col_transformer = any("ColumnTransformer" in s[1] for s in steps)
has_lr = any("LogisticRegression" in s[1] for s in steps)
check("Pipeline含ColumnTransformer", has_col_transformer, "可自动处理字符串列")
check("Pipeline含LogisticRegression", has_lr, "分类模型")

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 70)
print("验证汇总")
print("=" * 70)
passed = sum(1 for _, s, _ in results if s)
failed = sum(1 for _, s, _ in results if not s)
for name, status, detail in results:
    tag = "PASS" if status else "FAIL"
    print(f"  [{tag}] {name}: {detail}")
print(f"\n  总计: {passed} PASS, {failed} FAIL")
print(f"  状态: {'ALL PASS' if failed == 0 else 'HAS FAILURES'}")
print("=" * 70)

# 保存验证结果
output = ROOT / "结果/03_验证/final_verification_report.txt"
with open(output, "w", encoding="utf-8") as f:
    f.write("最终综合验证报告\n")
    f.write(f"日期: 2026-08-24\n")
    f.write("=" * 70 + "\n\n")
    for name, status, detail in results:
        tag = "PASS" if status else "FAIL"
        f.write(f"[{tag}] {name}: {detail}\n")
    f.write(f"\n总计: {passed} PASS, {failed} FAIL\n")
    f.write(f"状态: {'ALL PASS' if failed == 0 else 'HAS FAILURES'}\n")
print(f"\n报告已保存: {output}")
