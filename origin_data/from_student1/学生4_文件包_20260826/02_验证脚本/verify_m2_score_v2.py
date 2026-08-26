#!/usr/bin/env python3
"""
M2 risk_score 独立验证脚本 (v2)
Pipeline 模型使用 ColumnTransformer，可自动处理字符串列。
需要把 jurisdiction_context 重命名为 context_site_state 以匹配训练时的列名。

使用方法: cd /home/liu/osha && .venv/bin/python3 verify_m2_score_v2.py
"""
import sys
import joblib
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path("/home/liu/osha")
MODEL_PATH = ROOT / "结果/03_验证/frozen_model.joblib"
PROFILES_PATH = ROOT / "数据/02_分析数据/profiles_train_val.csv"
SUPPLEMENT_PATH = Path("/mnt/c/Users/LENOVO/Desktop/小刘skill/profile_supplement_8fields.csv")
OUTPUT_PATH = ROOT / "结果/03_验证/risk_score_verification.csv"

print("=" * 60)
print("M2 Risk Score 独立验证 (v2 - Pipeline)")
print("=" * 60)

# 1. 加载模型
print("\n[1] 加载 frozen_model.joblib...")
bundle = joblib.load(MODEL_PATH)
m2_pipeline = bundle["models"]["M2"]
stored_features = bundle["features"]
print(f"  M2 Pipeline steps: {[name for name, _ in m2_pipeline.steps]}")
print(f"  训练时特征列: {stored_features}")

# 2. 加载 profiles_train_val.csv
print("\n[2] 加载 profiles_train_val.csv...")
profiles = pd.read_csv(PROFILES_PATH, encoding="utf-8")
print(f"  行数: {len(profiles)}")
print(f"  split 分布: {profiles['split'].value_counts().to_dict()}")

# 3. 重命名 jurisdiction_context -> context_site_state
print("\n[3] 重命名 jurisdiction_context -> context_site_state...")
if "jurisdiction_context" in profiles.columns:
    profiles = profiles.rename(columns={"jurisdiction_context": "context_site_state"})
    print("  重命名完成")
elif "context_site_state" in profiles.columns:
    print("  列名已是 context_site_state，无需重命名")
else:
    print("  ERROR: 两列都不存在!")
    sys.exit(1)

# 4. 运行 Pipeline 预测
print("\n[4] 运行 M2 Pipeline 预测...")
scores = m2_pipeline.predict_proba(profiles)[:, 1]
profiles["recalculated_score"] = scores
print(f"  预测完成, 分数范围: [{scores.min():.6f}, {scores.max():.6f}]")
print(f"  分数均值: {scores.mean():.6f}")

# 5. 加载 supplement 并比对
print("\n[5] 加载 profile_supplement_8fields.csv 并比对...")
supplement = pd.read_csv(SUPPLEMENT_PATH, encoding="utf-8")
print(f"  supplement 行数: {len(supplement)}")

merged = profiles[["sample_id", "split", "recalculated_score"]].merge(
    supplement[["sample_id", "risk_score", "risk_percentile"]],
    on="sample_id",
    how="inner"
)
print(f"  匹配行数: {len(merged)}")

merged["diff"] = abs(merged["recalculated_score"] - merged["risk_score"])
max_diff = merged["diff"].max()
mean_diff = merged["diff"].mean()
exact_match = (merged["diff"] < 1e-6).sum()
close_match = (merged["diff"] < 1e-4).sum()

print(f"\n{'=' * 60}")
print(f"验证结果")
print(f"{'=' * 60}")
print(f"  总比对行数: {len(merged)}")
print(f"  精确匹配 (diff < 1e-6): {exact_match} ({exact_match/len(merged)*100:.1f}%)")
print(f"  近似匹配 (diff < 1e-4): {close_match} ({close_match/len(merged)*100:.1f}%)")
print(f"  最大差异: {max_diff:.8f}")
print(f"  平均差异: {mean_diff:.8f}")
print(f"  状态: {'PASS' if max_diff < 1e-4 else 'FAIL'}")

# 差异最大的5行
print(f"\n  差异最大的5行:")
worst = merged.nlargest(5, "diff")
for _, row in worst.iterrows():
    print(f"    {row['sample_id']} [{row['split']}]: 复算={row['recalculated_score']:.6f}, 原值={row['risk_score']:.6f}, 差异={row['diff']:.8f}")

# 6. 保存
merged.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
print(f"\n[6] 详细对比已保存: {OUTPUT_PATH}")
print(f"\n{'=' * 60}")
print("验证完成")
