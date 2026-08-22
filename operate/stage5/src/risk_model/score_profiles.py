"""用学生1 冻结的 R0/M1/M2 模型给画像卡打分。

输入：画像 CSV（学生3 交付的 profiles_train_val.csv 或学生1 的分析版）
输出：新增 score_r0 / score_m1 / score_m2 / risk_score / risk_percentile /
      model_version / score_evidence 的 CSV

注意：
1. 冻结模型用 `context_site_state` 作为 context 特征；学生3 新版画像
   把该列改名为 `jurisdiction_context`，本脚本默认做一次重命名，但
   语义等价性需要学生3/导师确认（见 docs/stage5_blockers.md）。
2. 冻结模型要求 sklearn；本脚本在缺少 sklearn 时会给出明确错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load_bundle(model_path: Path) -> dict:
    try:
        import joblib
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("缺少 joblib，无法加载冻结模型。请安装 requirements.txt 后再运行。") from exc
    return joblib.load(model_path)


def build_features(profiles: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    features = bundle["features"]
    context = list(features["context"])
    static = list(features["static"])
    dynamic = list(features["dynamic"])

    frame = profiles.copy()
    # 学生3 新版把 context_site_state 改名为 jurisdiction_context
    if "context_site_state" in context and "context_site_state" not in frame.columns and "jurisdiction_context" in frame.columns:
        print("WARNING: 输入用 jurisdiction_context 代替 context_site_state；语义等价性需人工确认。")
        frame["context_site_state"] = frame["jurisdiction_context"]

    categorical_cols = context
    numeric_cols = static + dynamic
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in categorical_cols:
        frame[col] = frame[col].astype(str)
    return frame[categorical_cols + numeric_cols]


def compute_scores(profiles: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    features = build_features(profiles, bundle)
    models = bundle["models"]
    cfg = bundle["config"]

    out = profiles[
        [c for c in ["sample_id", "entity_proxy_id", "quarter", "split"] if c in profiles.columns]
    ].copy()
    out["score_r0"] = pd.to_numeric(out_profile_smoothed(profiles), errors="coerce").fillna(0.5).clip(0, 1)
    out["score_m1"] = models["M1"].predict_proba(features)[:, 1]
    out["score_m2"] = models["M2"].predict_proba(features)[:, 1]
    out["risk_score"] = out["score_m2"]
    out["risk_percentile"] = out.groupby("quarter")["risk_score"].rank(pct=True)
    out["model_version"] = f"M2-v{cfg.get('model', {}).get('C', '?')}"
    out["score_evidence"] = cfg.get("model", {}).get("score_evidence", "frozen_model.joblib (见 frozen_manifest.json)")
    return out


def out_profile_smoothed(profiles: pd.DataFrame) -> pd.Series:
    if "smoothed_positive_rate" in profiles.columns:
        return profiles["smoothed_positive_rate"]
    return pd.Series(0.5, index=profiles.index)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True, help="画像 CSV 路径")
    parser.add_argument("--model", default="data/frozen/frozen_model.joblib")
    parser.add_argument("--out", default="data/profiles_with_score.csv")
    args = parser.parse_args()

    bundle = load_bundle(Path(args.model))
    profiles = pd.read_csv(args.profiles, low_memory=False)
    scored = compute_scores(profiles, bundle)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(out_path, index=False, encoding="utf-8")
    print(f"已生成 {out_path}，共 {len(scored)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
