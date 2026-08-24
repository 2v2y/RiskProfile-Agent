from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import platform
import sys
from pathlib import Path
from typing import Any

import joblib
import matplotlib
import numpy as np
import pandas as pd
import sklearn
import yaml
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from pipeline_common import DEFAULT_ROOT, PROGRAM_DIR, calibration_bins, ensure_dirs, fail_if_test_opened, load_config, quarterly_metrics, sha256_file, summarize_quarters, write_json


def make_model(categorical: list[str], numeric: list[str], cfg: dict[str, Any]) -> Pipeline:
    preprocess = ColumnTransformer([
        ("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical),
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)), ("scale", StandardScaler())]), numeric),
    ])
    return Pipeline([
        ("preprocess", preprocess),
        ("model", LogisticRegression(C=float(cfg["model"]["C"]), max_iter=int(cfg["model"]["max_iter"]), random_state=int(cfg["project"]["random_seed"]), solver="liblinear")),
    ])


def fit_models(train: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Pipeline]:
    y = train["label"].astype(int)
    if y.nunique() < 2:
        raise ValueError("训练集只有一类标签，逻辑回归无法拟合。")
    categorical = list(cfg["model"]["context_features"])
    static = list(cfg["model"]["static_features"])
    dynamic = list(cfg["model"]["dynamic_features"])
    models = {"M1": make_model(categorical, static, cfg), "M2": make_model(categorical, static + dynamic, cfg)}
    for model in models.values():
        model.fit(train, y)
    return models


def predict(frame: pd.DataFrame, models: dict[str, Pipeline]) -> pd.DataFrame:
    columns = ["sample_id", "entity_proxy_id", "quarter"] + (["label"] if "label" in frame.columns else [])
    result = frame[columns].copy()
    result["score_r0"] = pd.to_numeric(frame["smoothed_positive_rate"], errors="coerce").fillna(0.5).clip(0, 1)
    result["score_m1"] = models["M1"].predict_proba(frame)[:, 1]
    result["score_m2"] = models["M2"].predict_proba(frame)[:, 1]
    return result


def main(root: Path, config_path: Path | None = None) -> None:
    cfg = load_config(config_path)
    root = root.resolve()
    ensure_dirs(root)
    fail_if_test_opened(root, "03_fit_validate")
    test_dir = root / "结果/04_正式测试_封存"
    if (test_dir / "test_open_record.json").exists() or (test_dir / "test_open_attempt.json").exists():
        raise RuntimeError("Test已尝试开封；禁止重写冻结模型或Test预测。")
    out = root / "结果/03_验证"
    freeze_record_path = out / "model_freeze_record.json"
    if freeze_record_path.exists():
        raise RuntimeError("模型已经成功冻结；禁止覆盖Validation结果、模型、配置或Test预测。")
    profiles = pd.read_csv(root / "数据/02_分析数据/profiles_train_val.csv", low_memory=False)
    train = profiles.loc[profiles["split"].eq("train")].copy()
    validation = profiles.loc[profiles["split"].eq("validation")].copy()
    if train.empty or validation.empty:
        raise ValueError("Train或Validation为空；请核对时间切分与成熟规则。")
    models = fit_models(train, cfg)
    predictions = predict(validation, models)
    methods = {"R0": "score_r0", "M1": "score_m1", "M2": "score_m2"}
    metrics = quarterly_metrics(predictions, methods, float(cfg["model"]["review_fraction"]))
    summary = summarize_quarters(metrics)
    predictions.to_csv(out / "validation_predictions.csv", index=False, encoding="utf-8")
    metrics.to_csv(out / "validation_quarter_metrics.csv", index=False, encoding="utf-8")
    summary.to_csv(out / "validation_summary.csv", index=False, encoding="utf-8")
    calibration_bins(predictions, methods, int(cfg["model"]["calibration_bins"])).to_csv(out / "calibration_bins.csv", index=False, encoding="utf-8")
    write_json({
        "primary_comparison": "M2 minus M1",
        "validation_role": "audit_fixed_methods",
        "selection_performed": False,
        "test_labels_seen": False,
        "test_features_seen": False,
        "methods_frozen": ["R0", "M1", "M2"],
        "fixed_before_validation": {"logistic_C": float(cfg["model"]["C"]), "max_iter": int(cfg["model"]["max_iter"]), "feature_groups": cfg["model"], "review_fraction": float(cfg["model"]["review_fraction"])},
        "note": "Validation仅核对固定方法的可运行性与表现并形成冻结证据，不搜索或选择超参数；M1/M2同源样本、同一逻辑回归，M2只增加Dynamic。",
    }, out / "model_selection.json")

    final_models = fit_models(pd.concat([train, validation], ignore_index=True), cfg)
    bundle = {"models": final_models, "config": cfg, "features": {"context": cfg["model"]["context_features"], "static": cfg["model"]["static_features"], "dynamic": cfg["model"]["dynamic_features"]}}
    joblib.dump(bundle, out / "frozen_model.joblib")
    write_json(cfg, out / "frozen_config.json")
    write_json({
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
        "joblib": joblib.__version__,
        "PyYAML": yaml.__version__,
        "matplotlib": matplotlib.__version__,
    }, out / "environment_versions.json")
    tracked = {
        "profiles_train_val": root / "数据/02_分析数据/profiles_train_val.csv",
        "feature_dictionary": root / "数据/02_分析数据/feature_dictionary.csv",
        "test_features_commitment": root / "数据/03_封存测试/test_features_commitment.json",
        "sealed_test_commitment": root / "数据/03_封存测试/sealed_test_commitment.json",
        "sample_flow": root / "结果/01_数据审计/样本排除流.csv",
        "split_audit": root / "结果/01_数据审计/时间切分审计.csv",
        "maturity_audit": root / "结果/01_数据审计/成熟度审计.csv",
        "sample_flow_figure": root / "结果/01_数据审计/图2_样本筛选流程.svg",
        "profile_summary": root / "结果/02_画像/画像汇总.csv",
        "leakage_audit": root / "结果/02_画像/防泄漏审计.json",
        "research_framework_figure": root / "结果/02_画像/图1_动态画像研究框架.svg",
        "profile_trajectory_figure": root / "结果/02_画像/图3_匿名画像时间轨迹.svg",
        "profile_definition_freeze": root / "记录表/画像定义冻结.csv",
        "validation_predictions": out / "validation_predictions.csv",
        "validation_quarter_metrics": out / "validation_quarter_metrics.csv",
        "validation_summary": out / "validation_summary.csv",
        "validation_calibration": out / "calibration_bins.csv",
        "model_selection": out / "model_selection.json",
        "config": config_path.resolve() if config_path else PROGRAM_DIR / "config.yaml",
        "frozen_config": out / "frozen_config.json",
        "frozen_model": out / "frozen_model.joblib",
        "environment_versions": out / "environment_versions.json",
        "prepare_script": PROGRAM_DIR / "01_prepare_data.py",
        "profile_script": PROGRAM_DIR / "02_build_profiles.py",
        "fit_script": PROGRAM_DIR / "03_fit_validate.py",
        "common_module": PROGRAM_DIR / "pipeline_common.py",
        "sealed_test_script": PROGRAM_DIR / "04_run_sealed_test.py",
        "sealed_prediction_script": PROGRAM_DIR / "03b_generate_sealed_predictions.py",
        "paper_table_script": PROGRAM_DIR / "05_make_paper_tables.py",
        "requirements": PROGRAM_DIR / "requirements.txt",
    }
    missing_tracked = [str(path) for path in tracked.values() if not path.exists()]
    if missing_tracked:
        raise RuntimeError(f"模型冻结缺少必需上游/程序证据: {missing_tracked}")
    optional_tracked = {
        "download_manifest": root / "数据/00_原始数据/download_manifest.json",
        "field_rule_snapshot": root / "结果/01_数据审计/字段与规则快照.json",
        "entity_audit_result": root / "结果/01_数据审计/实体人工复核结果.json",
        "scale_processing_audit": root / "结果/01_数据审计/规模处理审计.json",
        "restricted_permission_audit": root / "结果/01_数据审计/受限目录权限审计.json",
    }
    tracked.update({name: path for name, path in optional_tracked.items() if path.exists()})
    if bool(cfg["rules"].get("require_entity_audit_gate", True)):
        required_formal = {"download_manifest", "field_rule_snapshot", "entity_audit_result", "scale_processing_audit", "restricted_permission_audit"}
        missing_formal = sorted(required_formal - set(tracked))
        if missing_formal:
            raise RuntimeError(f"正式模型冻结缺少上游门禁证据: {missing_formal}")
    manifest_path = out / "frozen_manifest.json"
    write_json({
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": "pre-test-freeze-v1",
        "files": {name: {"path": str(path.relative_to(root)) if path.is_relative_to(root) else str(path), "sha256": sha256_file(path)} for name, path in tracked.items()},
    }, manifest_path)
    freeze_record = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "frozen",
        "frozen_manifest_path": str(manifest_path.relative_to(root)),
        "frozen_manifest_sha256": sha256_file(manifest_path),
        "test_labels_read": False,
        "test_features_read": False,
        "test_commitment_sha256": sha256_file(root / "数据/03_封存测试/sealed_test_commitment.json"),
        "test_features_commitment_sha256": sha256_file(root / "数据/03_封存测试/test_features_commitment.json"),
    }
    with freeze_record_path.open("x", encoding="utf-8") as handle:
        json.dump(freeze_record, handle, ensure_ascii=False, indent=2)
    print("03完成: 固定方法的Validation审计与模型已冻结；未读取Test特征/标签，也未生成Test预测。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    try:
        main(args.root, args.config)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
