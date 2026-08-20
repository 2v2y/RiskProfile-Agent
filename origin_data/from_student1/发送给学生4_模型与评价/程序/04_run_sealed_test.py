from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline_common import DEFAULT_ROOT, PROGRAM_DIR, calibration_bins, ensure_dirs, load_config, quarterly_metrics, secure_restricted_tree, sha256_file, summarize_quarters, write_json


METHODS = {"R0": "score_r0", "M1": "score_m1", "M2": "score_m2"}


def validate_open_approval(root: Path, expected_artifacts: dict[str, tuple[Path, str]], expected_values: dict[str, str]) -> dict:
    """Validate the final approval row and return an immutable snapshot payload.

    The source register is a workflow log: after opening, its status may legitimately
    change from ``批准开封`` to ``已完成``.  Therefore downstream evidence must anchor
    the exact approved row captured here, rather than the mutable CSV as it exists
    later.
    """
    approval_path = root / "记录表/测试开封记录.csv"
    if not approval_path.exists():
        raise RuntimeError("缺少记录表/测试开封记录.csv；门0—门3尚未形成可核验的开封批准。")
    approval = pd.read_csv(approval_path, dtype=str).fillna("")
    if approval.empty:
        raise RuntimeError("测试开封记录没有批准行；禁止开封。")
    row = approval.iloc[-1]
    gate_columns = [f"门{index}状态" for index in range(4)]
    gate_people = [f"门{index}确认人" for index in range(4)]
    required_nonempty = [
        "方法冻结时间", "批准开封时间", "执行人", "监督人", "复核人", "首次执行确认", "状态",
        *gate_columns, *gate_people,
        *[column for name in expected_artifacts for column in (f"{name}文件", f"{name}SHA256")],
    ]
    missing = [column for column in required_nonempty if column not in approval.columns or not str(row.get(column, "")).strip()]
    if missing:
        raise RuntimeError(f"测试开封记录的开封前必填项不完整: {missing}")
    if str(row["状态"]).strip() != "批准开封":
        raise RuntimeError("测试开封记录最后一行状态必须为“批准开封”。")
    if any(str(row[column]).strip() != "通过" for column in gate_columns):
        raise RuntimeError("测试开封记录最后一行的门0—门3状态必须全部为“通过”。")
    if str(row["首次执行确认"]).strip().upper() not in {"是", "Y", "YES", "TRUE", "1"}:
        raise RuntimeError("测试开封记录必须明确“首次执行确认=是”。")
    people = {str(row[column]).strip() for column in ["执行人", "监督人", "复核人"]}
    if len(people) < 3:
        raise RuntimeError("Test开封的执行人、监督人和复核人必须是三名不同人员。")
    expected: dict[str, str] = {}
    for name, (path, expected_hash) in expected_artifacts.items():
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        expected[f"{name}文件"] = rel.replace("\\", "/")
        expected[f"{name}SHA256"] = expected_hash
    expected.update(expected_values)
    approved_values = {column: str(row.get(column, "")).strip().replace("\\", "/") for column in expected}
    mismatched = [column for column, value in expected.items() if approved_values[column] != value]
    if mismatched:
        raise RuntimeError(f"测试开封记录中的冻结SHA-256不匹配: {mismatched}")
    command = str(row.get("执行命令", "")).strip()
    allowed_commands = {
        "python3 程序/04_run_sealed_test.py --confirm-open-test",
        "python 程序/04_run_sealed_test.py --confirm-open-test",
    }
    if command and command not in allowed_commands:
        raise RuntimeError("测试开封记录中的执行命令不是唯一正式入口。")
    approved_fields = {str(column): str(row.get(column, "")).strip() for column in approval.columns}
    return {
        "status": "approved_snapshot",
        "snapshotted_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_csv_path": str(approval_path.relative_to(root)).replace("\\", "/"),
        "source_csv_sha256": sha256_file(approval_path),
        "source_row_number": int(len(approval) + 1),
        "approved_fields": approved_fields,
        "matching_validation": {
            "validation_passed": True,
            "status_is_approved": True,
            "distinct_people_count": len(people),
            "command_is_allowed": not command or command in allowed_commands,
            "artifacts_and_values": {
                column: {
                    "approved": approved_values[column],
                    "expected": expected[column],
                    "matched": approved_values[column] == expected[column],
                }
                for column in expected
            },
        },
    }


def validate_frozen_manifest(root: Path, frozen_manifest: dict, cfg: dict) -> None:
    required = {
        "profiles_train_val", "feature_dictionary", "test_features_commitment", "sealed_test_commitment",
        "sample_flow", "split_audit", "maturity_audit", "sample_flow_figure", "profile_summary", "leakage_audit",
        "research_framework_figure", "profile_trajectory_figure", "profile_definition_freeze",
        "validation_predictions", "validation_quarter_metrics", "validation_summary", "validation_calibration", "model_selection",
        "config", "frozen_config", "frozen_model", "environment_versions", "prepare_script", "profile_script", "fit_script", "common_module",
        "sealed_prediction_script", "sealed_test_script", "paper_table_script", "requirements",
    }
    if bool(cfg["rules"].get("require_entity_audit_gate", True)):
        required |= {"download_manifest", "field_rule_snapshot", "entity_audit_result", "scale_processing_audit"}
    missing = sorted(required - set(frozen_manifest.get("files", {})))
    if missing:
        raise RuntimeError(f"冻结清单缺少必需证据键: {missing}")
    for name, detail in frozen_manifest["files"].items():
        raw_path = Path(detail["path"])
        path = raw_path if raw_path.is_absolute() else root / raw_path
        if not path.exists() or sha256_file(path) != detail["sha256"]:
            raise RuntimeError(f"冻结文件已缺失或变更，禁止开封: {name} -> {detail['path']}")


def bootstrap(predictions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    iterations = int(cfg["project"]["bootstrap_iterations"])
    if iterations < 2000:
        raise ValueError("entity-cluster paired bootstrap次数必须不少于2000。")
    entities = np.array(sorted(predictions["entity_proxy_id"].unique()))
    if len(entities) < 2:
        raise ValueError("Test实体数少于2，无法进行聚类Bootstrap。")
    rng = np.random.default_rng(int(cfg["project"]["random_seed"]))
    records = []
    for iteration in range(iterations):
        draws = rng.choice(entities, size=len(entities), replace=True)
        counts = pd.Series(draws).value_counts().rename_axis("entity_proxy_id").rename("bootstrap_weight")
        sampled = predictions.merge(counts, on="entity_proxy_id", how="inner")
        qm = quarterly_metrics(sampled, METHODS, float(cfg["model"]["review_fraction"]), weight_col="bootstrap_weight")
        summary = summarize_quarters(qm).set_index("method")
        records.append({
            "iteration": iteration,
            "delta_ap_m2_m1": summary.loc["M2", "ap"] - summary.loc["M1", "ap"],
            "delta_recall_at_20_m2_m1": summary.loc["M2", "recall_at_20"] - summary.loc["M1", "recall_at_20"],
        })
    draws = pd.DataFrame(records)
    observed = summarize_quarters(quarterly_metrics(predictions, METHODS, float(cfg["model"]["review_fraction"]))).set_index("method")
    observed_values = {"delta_ap_m2_m1": observed.loc["M2", "ap"] - observed.loc["M1", "ap"], "delta_recall_at_20_m2_m1": observed.loc["M2", "recall_at_20"] - observed.loc["M1", "recall_at_20"]}
    result = []
    for metric in ["delta_ap_m2_m1", "delta_recall_at_20_m2_m1"]:
        values = draws[metric].dropna().to_numpy()
        if not len(values):
            raise RuntimeError(f"Bootstrap的{metric}无有效抽样值。")
        result.append({"metric": metric, "iterations": iterations, "valid_iterations": len(values), "estimate": observed_values[metric], "bootstrap_mean": values.mean(), "ci_2.5pct": np.quantile(values, 0.025), "ci_97.5pct": np.quantile(values, 0.975)})
    return pd.DataFrame(result)


def execute_open(root: Path, cfg: dict, out: Path) -> None:
    record_path = out / "test_open_record.json"
    frozen_cfg_path = root / "结果/03_验证/frozen_config.json"
    canonical_cfg = json.loads(json.dumps(cfg, ensure_ascii=False, default=str))
    if json.loads(frozen_cfg_path.read_text(encoding="utf-8")) != canonical_cfg:
        raise RuntimeError("当前config与Validation后的frozen_config.json不一致；禁止开封。")
    prediction_path = root / "结果/03_验证/test_predictions_sealed.csv"
    prediction_commitment_path = root / "结果/03_验证/test_prediction_commitment.json"
    label_path = root / "数据/03_封存测试/sealed_test_labels.csv"
    commitment_path = root / "数据/03_封存测试/sealed_test_commitment.json"
    feature_commitment_path = root / "数据/03_封存测试/test_features_commitment.json"
    frozen_manifest_path = root / "结果/03_验证/frozen_manifest.json"
    freeze_record_path = root / "结果/03_验证/model_freeze_record.json"
    if not freeze_record_path.exists():
        raise RuntimeError("缺少model_freeze_record.json；模型尚未完成不可覆盖冻结。")
    freeze_record = json.loads(freeze_record_path.read_text(encoding="utf-8"))
    if freeze_record.get("status") != "frozen" or freeze_record.get("frozen_manifest_sha256") != sha256_file(frozen_manifest_path):
        raise RuntimeError("模型冻结记录与当前frozen_manifest.json不一致。")
    frozen_manifest = json.loads(frozen_manifest_path.read_text(encoding="utf-8"))
    validate_frozen_manifest(root, frozen_manifest, cfg)
    commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
    if commitment.get("path") != str(label_path.relative_to(root)) or int(commitment.get("rows", -1)) < 0:
        raise RuntimeError("Test标签commitment的路径或行数无效。")
    feature_commitment = json.loads(feature_commitment_path.read_text(encoding="utf-8"))
    expected_feature_path = root / str(feature_commitment.get("path", ""))
    if expected_feature_path != root / "数据/03_封存测试/test_features_sealed.csv" or int(feature_commitment.get("rows", -1)) < 0:
        raise RuntimeError("Test特征commitment的路径或行数无效。")
    if not prediction_commitment_path.exists():
        raise RuntimeError("缺少03b生成的test_prediction_commitment.json；禁止开封。")
    prediction_commitment = json.loads(prediction_commitment_path.read_text(encoding="utf-8"))
    if (
        prediction_commitment.get("path") != str(prediction_path.relative_to(root))
        or not prediction_path.exists()
        or prediction_commitment.get("sha256") != sha256_file(prediction_path)
        or prediction_commitment.get("test_features_commitment_sha256") != sha256_file(feature_commitment_path)
        or prediction_commitment.get("frozen_manifest_sha256") != sha256_file(frozen_manifest_path)
        or prediction_commitment.get("frozen_model_sha256") != sha256_file(root / "结果/03_验证/frozen_model.joblib")
        or prediction_commitment.get("labels_read") is not False
    ):
        raise RuntimeError("Test预测commitment与冻结模型/特征commitment/预测文件不一致。")
    approval_artifacts = {
        "冻结清单": (frozen_manifest_path, sha256_file(frozen_manifest_path)),
        "模型": (root / "结果/03_验证/frozen_model.joblib", sha256_file(root / "结果/03_验证/frozen_model.joblib")),
        "配置": (frozen_cfg_path, sha256_file(frozen_cfg_path)),
        "04程序": (PROGRAM_DIR / "04_run_sealed_test.py", sha256_file(PROGRAM_DIR / "04_run_sealed_test.py")),
        "Validation汇总": (root / "结果/03_验证/validation_summary.csv", sha256_file(root / "结果/03_验证/validation_summary.csv")),
        "标签承诺": (commitment_path, sha256_file(commitment_path)),
        "Test特征承诺": (feature_commitment_path, sha256_file(feature_commitment_path)),
        "Test输入": (expected_feature_path, str(feature_commitment.get("sha256", ""))),
        "Test预测承诺": (prediction_commitment_path, sha256_file(prediction_commitment_path)),
        "Test预测": (prediction_path, str(prediction_commitment.get("sha256", ""))),
        "Test标签": (label_path, str(commitment.get("sha256", ""))),
    }
    approval_snapshot = validate_open_approval(root, approval_artifacts, {})
    approval_snapshot_path = out / "test_open_approval_snapshot.json"
    # 在首次读取标签文件字节前，以独占创建方式固化当时已批准的最后一行。
    # 原始CSV随后可以把状态更新为“已完成”，但不得改变本快照及其证据链。
    with approval_snapshot_path.open("x", encoding="utf-8") as handle:
        json.dump(approval_snapshot, handle, ensure_ascii=False, indent=2)
    # attempt已在main中独占写入；只有完成上面全部非标签门禁并固化批准快照后，才首次读取标签文件字节。
    if not label_path.exists() or sha256_file(label_path) != commitment.get("sha256"):
        raise RuntimeError("封存Test标签与学生1生成的预封存commitment不一致。")
    predictions = pd.read_csv(prediction_path, dtype={"sample_id": str, "entity_proxy_id": str})
    labels = pd.read_csv(label_path, dtype={"sample_id": str, "entity_proxy_id": str})
    if len(predictions) != int(prediction_commitment.get("rows", -1)) or list(predictions.columns) != prediction_commitment.get("columns"):
        raise RuntimeError("Test预测行数/列顺序与prediction commitment不一致。")
    if "label" in predictions.columns:
        raise RuntimeError("封存预测文件不应含label。")
    if labels["label"].isna().any() or labels["sample_id"].duplicated().any() or predictions["sample_id"].duplicated().any():
        raise RuntimeError("Test标签或预测主键不完整/不唯一。")
    merged = predictions.merge(labels[["sample_id", "entity_proxy_id", "quarter", "label"]], on=["sample_id", "entity_proxy_id", "quarter"], how="outer", validate="one_to_one", indicator=True)
    if not merged["_merge"].eq("both").all():
        raise RuntimeError("Test预测和标签sample_id不完全一致。")
    merged = merged.drop(columns="_merge")
    merged["label"] = merged["label"].astype(int)
    max_test = pd.Timestamp(cfg["splits"]["test_end"]).to_period("Q")
    if any(pd.Period(q, freq="Q") > max_test for q in merged["quarter"]):
        raise RuntimeError("Test包含2025Q4之后的季度，超出冻结边界。")
    expected_quarters = {str(q) for q in pd.period_range(pd.Timestamp(cfg["splits"]["test_start"]).to_period("Q"), max_test, freq="Q")}
    actual_quarters = set(merged["quarter"].astype(str))
    if actual_quarters != expected_quarters:
        raise RuntimeError(f"Test季度不完整；缺失={sorted(expected_quarters - actual_quarters)}, 多出={sorted(actual_quarters - expected_quarters)}。不能默认报告少于8季度。")
    maturity_path = root / "结果/01_数据审计/成熟度审计.csv"
    maturity = pd.read_csv(maturity_path, dtype={"quarter": str})
    test_maturity = maturity.loc[maturity["split"].eq("test"), ["quarter", "candidate_total", "mature_total", "immature_open_case", "immature_other", "mature_rate", "maturity_boundary"]].copy()
    if set(test_maturity["quarter"]) != expected_quarters:
        raise RuntimeError("成熟度审计的Test季度集合与冻结Test边界不一致。")
    prediction_counts = merged.groupby("quarter").size()
    for row in test_maturity.itertuples(index=False):
        if int(row.mature_total) != int(prediction_counts.get(row.quarter, -1)):
            raise RuntimeError(f"{row.quarter}成熟样本数与Test预测/标签数不一致。")
    metrics = quarterly_metrics(merged, METHODS, float(cfg["model"]["review_fraction"]))
    summary = summarize_quarters(metrics)
    ci = bootstrap(merged, cfg)
    recall_pivot = metrics.pivot(index="quarter", columns="method", values="recall_at_20")
    positive_directions = int(((recall_pivot["M2"] - recall_pivot["M1"]) > 0).sum())
    criteria = {
        "c1_ap_ci_lower_above_zero": bool(ci.set_index("metric").loc["delta_ap_m2_m1", "ci_2.5pct"] > 0),
        "c2_recall_ci_lower_above_zero": bool(ci.set_index("metric").loc["delta_recall_at_20_m2_m1", "ci_2.5pct"] > 0),
        "c2_positive_quarters": positive_directions,
        "c2_requires_at_least_5_of_8": positive_directions >= 5 and len(recall_pivot) == 8,
        "all_test_quarters_have_positive": bool((merged.groupby("quarter")["label"].sum() > 0).all()),
        "interpretation_downgraded_if_any_quarter_has_zero_positive": bool((merged.groupby("quarter")["label"].sum() == 0).any()),
    }
    with tempfile.TemporaryDirectory(prefix=".test_open_", dir=out) as temp_dir:
        temp_out = Path(temp_dir)
        merged.to_csv(temp_out / "test_predictions_with_labels.csv", index=False, encoding="utf-8")
        metrics.to_csv(temp_out / "test_quarter_metrics.csv", index=False, encoding="utf-8")
        summary.to_csv(temp_out / "test_summary.csv", index=False, encoding="utf-8")
        ci.to_csv(temp_out / "bootstrap_ci.csv", index=False, encoding="utf-8")
        calibration_bins(merged, METHODS, int(cfg["model"]["calibration_bins"])).to_csv(temp_out / "calibration_bins.csv", index=False, encoding="utf-8")
        test_maturity.to_csv(temp_out / "test_maturity_by_quarter.csv", index=False, encoding="utf-8")
        result_files = [
            "test_predictions_with_labels.csv",
            "test_quarter_metrics.csv",
            "test_summary.csv",
            "bootstrap_ci.csv",
            "calibration_bins.csv",
            "test_maturity_by_quarter.csv",
        ]
        write_json({
            "opened_at_utc": datetime.now(timezone.utc).isoformat(),
            "confirmation_flag": "--confirm-open-test",
            "prediction_sha256_before_open": sha256_file(prediction_path),
            "label_sha256_before_open": sha256_file(label_path),
            "frozen_config_sha256": sha256_file(frozen_cfg_path),
            "frozen_model_sha256": sha256_file(root / "结果/03_验证/frozen_model.joblib"),
            "frozen_manifest_sha256": sha256_file(frozen_manifest_path),
            "model_freeze_record_sha256": sha256_file(freeze_record_path),
            "sealed_test_commitment_sha256": sha256_file(commitment_path),
            "test_features_commitment_sha256": sha256_file(feature_commitment_path),
            "test_prediction_commitment_sha256": sha256_file(prediction_commitment_path),
            "test_open_approval_snapshot_sha256": sha256_file(approval_snapshot_path),
            "test_open_approval_source_csv_sha256": approval_snapshot["source_csv_sha256"],
            "open_script_sha256": sha256_file(PROGRAM_DIR / "04_run_sealed_test.py"),
            "common_module_sha256": sha256_file(PROGRAM_DIR / "pipeline_common.py"),
            "rows": len(merged),
            "quarters": sorted(merged["quarter"].unique()),
            "bootstrap_iterations": int(cfg["project"]["bootstrap_iterations"]),
            "pre_registered_criteria": criteria,
            "test_maturity_by_quarter": test_maturity.to_dict("records"),
            "output_sha256": {name: sha256_file(temp_out / name) for name in result_files},
        }, temp_out / record_path.name)
        for path in sorted(temp_out.iterdir()):
            path.replace(out / path.name)
    permission_audit = secure_restricted_tree(root, include_test_results=True)
    if permission_audit.get("supported") is not True:
        raise RuntimeError("正式Test结果无法落实POSIX 0700/0600权限门。")
    print("04完成: 封存Test已一次性开封，不得覆盖原结果。")


def update_attempt(path: Path, payload: dict) -> None:
    temp_path = path.with_suffix(".tmp")
    write_json(payload, temp_path)
    temp_path.replace(path)
    path.chmod(0o600)


def main(root: Path, confirm: bool, config_path: Path | None = None) -> None:
    if not confirm:
        raise RuntimeError("封存Test未开封。只有完成开封确认后才能使用 --confirm-open-test。")
    cfg = load_config(config_path)
    root = root.resolve()
    ensure_dirs(root)
    out = root / "结果/04_正式测试_封存"
    out.chmod(0o700)
    attempt_path = out / "test_open_attempt.json"
    existing = [path for path in out.iterdir() if path.is_file() and path.name != "README_正式测试结果说明.md"]
    if existing:
        raise RuntimeError(f"正式Test目录已有文件，程序拒绝再次开封: {[p.name for p in existing]}")
    tracked_inputs = {
        "prediction": root / "结果/03_验证/test_predictions_sealed.csv",
        "prediction_commitment": root / "结果/03_验证/test_prediction_commitment.json",
        "test_commitment": root / "数据/03_封存测试/sealed_test_commitment.json",
        "test_features_commitment": root / "数据/03_封存测试/test_features_commitment.json",
        "test_open_approval": root / "记录表/测试开封记录.csv",
        "frozen_config": root / "结果/03_验证/frozen_config.json",
        "frozen_model": root / "结果/03_验证/frozen_model.joblib",
        "frozen_manifest": root / "结果/03_验证/frozen_manifest.json",
        "model_freeze_record": root / "结果/03_验证/model_freeze_record.json",
    }
    attempt = {
        "attempted_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "started",
        "confirmation_flag": "--confirm-open-test",
        "input_sha256": {name: sha256_file(path) if path.exists() else None for name, path in tracked_inputs.items()},
    }
    with attempt_path.open("x", encoding="utf-8") as handle:
        json.dump(attempt, handle, ensure_ascii=False, indent=2)
    attempt_path.chmod(0o600)
    try:
        execute_open(root, cfg, out)
    except Exception as exc:
        attempt.update({"status": "failed", "finished_at_utc": datetime.now(timezone.utc).isoformat(), "error_type": type(exc).__name__})
        update_attempt(attempt_path, attempt)
        raise
    open_record_path = out / "test_open_record.json"
    open_record = json.loads(open_record_path.read_text(encoding="utf-8"))
    attempt.update({
        "status": "success",
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "test_open_record_sha256": sha256_file(open_record_path),
        "test_open_approval_snapshot_sha256": sha256_file(out / "test_open_approval_snapshot.json"),
        "result_output_sha256": open_record.get("output_sha256", {}),
    })
    update_attempt(attempt_path, attempt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--confirm-open-test", action="store_true")
    args = parser.parse_args()
    try:
        main(args.root, args.confirm_open_test, args.config)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
