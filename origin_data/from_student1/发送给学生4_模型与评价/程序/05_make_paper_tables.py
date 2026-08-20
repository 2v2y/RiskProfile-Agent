from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
import pandas as pd

from pipeline_common import DEFAULT_ROOT, ensure_dirs, sha256_file


INDEX_COLUMNS = [
    "unique_key", "stage", "split", "quarter", "method_or_comparison",
    "metric", "value", "source_file", "source_row", "source_column", "source_sha256",
]


def verify_frozen_and_opened_sources(root: Path) -> dict[str, Any] | None:
    """Verify the frozen chain and, if opened, every immutable Test result file."""
    validation = root / "结果/03_验证"
    frozen_manifest_path = validation / "frozen_manifest.json"
    if not frozen_manifest_path.exists():
        raise RuntimeError("缺少frozen_manifest.json；必须先完成03模型冻结。")
    frozen_manifest = json.loads(frozen_manifest_path.read_text(encoding="utf-8"))
    tracked_resolved: set[Path] = set()
    for name, detail in frozen_manifest.get("files", {}).items():
        raw_path = Path(detail["path"])
        path = raw_path if raw_path.is_absolute() else root / raw_path
        if not path.exists() or sha256_file(path) != detail["sha256"]:
            raise RuntimeError(f"冻结来源缺失或已改变，停止制表: {name} -> {detail['path']}")
        tracked_resolved.add(path.resolve())
    required_table_sources = [
        root / "结果/01_数据审计/样本排除流.csv",
        root / "结果/01_数据审计/时间切分审计.csv",
        root / "结果/01_数据审计/成熟度审计.csv",
        root / "数据/02_分析数据/feature_dictionary.csv",
        root / "结果/02_画像/画像汇总.csv",
        root / "结果/03_验证/validation_quarter_metrics.csv",
        root / "结果/03_验证/validation_summary.csv",
    ]
    missing_from_freeze = [str(path.relative_to(root)) for path in required_table_sources if path.resolve() not in tracked_resolved]
    if missing_from_freeze:
        raise RuntimeError(f"论文表/数字来源未纳入模型冻结清单: {missing_from_freeze}")

    test_dir = root / "结果/04_正式测试_封存"
    attempt_path = test_dir / "test_open_attempt.json"
    record_path = test_dir / "test_open_record.json"
    if not attempt_path.exists() and not record_path.exists():
        return None
    if not attempt_path.exists() or not record_path.exists():
        raise RuntimeError("Test存在不完整的开封痕迹；停止生成正式Test表。")
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    if attempt.get("status") != "success":
        raise RuntimeError("Test开封未成功；保留失败记录并停止生成正式Test表。")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if attempt.get("test_open_record_sha256") != sha256_file(record_path):
        raise RuntimeError("开封attempt记录的test_open_record SHA-256与当前文件不一致。")
    if record.get("frozen_manifest_sha256") != sha256_file(frozen_manifest_path):
        raise RuntimeError("开封记录中的冻结清单SHA-256与当前文件不一致。")
    approval_snapshot_path = test_dir / "test_open_approval_snapshot.json"
    if not approval_snapshot_path.exists():
        raise RuntimeError("缺少开封前固化的批准行快照；停止生成正式Test表。")
    approval_snapshot_hash = sha256_file(approval_snapshot_path)
    if record.get("test_open_approval_snapshot_sha256") != approval_snapshot_hash:
        raise RuntimeError("开封记录与批准行快照SHA-256不一致。")
    if attempt.get("test_open_approval_snapshot_sha256") != approval_snapshot_hash:
        raise RuntimeError("开封attempt与批准行快照SHA-256不一致。")
    approval_snapshot = json.loads(approval_snapshot_path.read_text(encoding="utf-8"))
    approval_fields = approval_snapshot.get("approved_fields", {})
    approval_validation = approval_snapshot.get("matching_validation", {})
    approval_matches = approval_validation.get("artifacts_and_values", {})
    source_csv_hash = str(approval_snapshot.get("source_csv_sha256", ""))
    if (
        approval_snapshot.get("status") != "approved_snapshot"
        or approval_snapshot.get("source_csv_path") != "记录表/测试开封记录.csv"
        or len(source_csv_hash) != 64
        or approval_fields.get("状态") != "批准开封"
        or approval_validation.get("validation_passed") is not True
        or not approval_matches
        or not all(detail.get("matched") is True for detail in approval_matches.values())
        or record.get("test_open_approval_source_csv_sha256") != source_csv_hash
    ):
        raise RuntimeError("批准行快照字段或匹配校验不完整；停止生成正式Test表。")
    # 原始测试开封记录是可追加/更新状态的流程日志。这里只要求其仍存在；
    # 正式证据锚定上面的不可变批准行快照，不再要求当前CSV哈希保持不变。
    if not (root / approval_snapshot["source_csv_path"]).exists():
        raise RuntimeError("测试开封记录源CSV已缺失；停止生成正式Test表。")
    post_freeze_sources = {
        "model_freeze_record_sha256": root / "结果/03_验证/model_freeze_record.json",
        "sealed_test_commitment_sha256": root / "数据/03_封存测试/sealed_test_commitment.json",
        "test_features_commitment_sha256": root / "数据/03_封存测试/test_features_commitment.json",
        "test_prediction_commitment_sha256": root / "结果/03_验证/test_prediction_commitment.json",
    }
    for key, path in post_freeze_sources.items():
        if not path.exists() or record.get(key) != sha256_file(path):
            raise RuntimeError(f"开封记录与当前冻结/批准证据不一致: {key}")

    fixed_inputs = {
        "prediction_sha256_before_open": validation / "test_predictions_sealed.csv",
        "label_sha256_before_open": root / "数据/03_封存测试/sealed_test_labels.csv",
        "frozen_config_sha256": validation / "frozen_config.json",
        "frozen_model_sha256": validation / "frozen_model.joblib",
    }
    for key, path in fixed_inputs.items():
        if not path.exists() or record.get(key) != sha256_file(path):
            raise RuntimeError(f"开封记录与当前冻结输入不一致: {key}")
    commitment = json.loads((root / "数据/03_封存测试/sealed_test_commitment.json").read_text(encoding="utf-8"))
    if commitment.get("sha256") != record.get("label_sha256_before_open"):
        raise RuntimeError("Test标签实际SHA-256与预封存commitment不一致。")
    output_hashes = record.get("output_sha256")
    if not isinstance(output_hashes, dict) or not output_hashes:
        raise RuntimeError("开封记录缺少Test结果文件SHA-256；不能建立论文证据链。")
    if attempt.get("result_output_sha256") != output_hashes:
        raise RuntimeError("开封attempt与open_record登记的结果SHA-256不一致。")
    for name, expected in output_hashes.items():
        path = test_dir / name
        if not path.exists() or sha256_file(path) != expected:
            raise RuntimeError(f"正式Test结果缺失或已改变: {name}")
    return record


def copy_table(source: Path, destination: Path) -> dict[str, Any]:
    frame = pd.read_csv(source)
    frame.to_csv(destination, index=False, encoding="utf-8-sig")
    return {
        "artifact": destination.name,
        "source": str(source),
        "rows": len(frame),
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(destination),
    }


def interpret_test_conclusion(criteria: dict[str, Any], ap_estimate: float, recall_estimate: float) -> dict[str, Any]:
    """Apply the frozen all-quarter-positive evidence gate before any support claim."""
    all_positive = bool(criteria.get("all_test_quarters_have_positive", False))
    c1 = bool(criteria.get("c1_ap_ci_lower_above_zero", False))
    c2 = bool(
        criteria.get("c2_recall_ci_lower_above_zero", False)
        and criteria.get("c2_requires_at_least_5_of_8", False)
    )
    if not all_positive:
        interpretation = "证据不足：Test至少一个季度无阳性，冻结成功判据不可完整解释"
    elif c1 and c2:
        interpretation = "支持当前协议下的稳定增量排序价值"
    elif float(ap_estimate) > 0 or float(recall_estimate) > 0:
        interpretation = "存在改善迹象，但未同时满足冻结判据"
    else:
        interpretation = "未观察到稳定的动态增量价值"
    return {
        "all_test_quarters_have_positive": all_positive,
        "evidence_gate_met": all_positive,
        "c1_met": c1,
        "c2_met": c2,
        "overall_support_met": bool(all_positive and c1 and c2),
        "overall_interpretation": interpretation,
    }


def build_test_table(root: Path, record: dict[str, Any], destination: Path) -> dict[str, Any]:
    test_dir = root / "结果/04_正式测试_封存"
    summary_path = test_dir / "test_summary.csv"
    ci_path = test_dir / "bootstrap_ci.csv"
    quarter_path = test_dir / "test_quarter_metrics.csv"
    summary = pd.read_csv(summary_path)
    ci = pd.read_csv(ci_path).set_index("metric")
    quarter_metrics = pd.read_csv(quarter_path)
    criteria = record["pre_registered_criteria"]
    positive_counts = quarter_metrics.pivot_table(index="quarter", columns="method", values="positives", aggfunc="first")
    if positive_counts.empty or positive_counts.isna().any().any() or positive_counts.nunique(axis=1).gt(1).any():
        raise RuntimeError("Test季度方法表中的positives缺失或方法间不一致，停止形成结论。")
    recomputed_all_positive = bool((positive_counts.iloc[:, 0] > 0).all())
    if recomputed_all_positive != bool(criteria.get("all_test_quarters_have_positive", False)):
        raise RuntimeError("Test季度指标复算的全季度阳性总闸与open_record不一致。")

    rows: list[dict[str, Any]] = []
    for row in summary.to_dict("records"):
        rows.append({
            "row_type": "method",
            "method_or_comparison": row["method"],
            "quarters": row.get("quarters"),
            "n": row.get("n"),
            "positives": row.get("positives"),
            "ap": row.get("ap"),
            "recall_at_20": row.get("recall_at_20"),
            "precision_at_20": row.get("precision_at_20"),
            "brier": row.get("brier"),
        })

    ap = ci.loc["delta_ap_m2_m1"]
    recall = ci.loc["delta_recall_at_20_m2_m1"]
    conclusion = interpret_test_conclusion(criteria, float(ap["estimate"]), float(recall["estimate"]))
    rows.append({
        "row_type": "comparison",
        "method_or_comparison": "M2-M1",
        "delta_ap": ap["estimate"],
        "delta_ap_ci_lower": ap["ci_2.5pct"],
        "delta_ap_ci_upper": ap["ci_97.5pct"],
        "delta_recall_at_20": recall["estimate"],
        "delta_recall_ci_lower": recall["ci_2.5pct"],
        "delta_recall_ci_upper": recall["ci_97.5pct"],
        "bootstrap_valid_ap": int(ap["valid_iterations"]),
        "bootstrap_valid_recall": int(recall["valid_iterations"]),
        "positive_recall_quarters": int(criteria["c2_positive_quarters"]),
        **conclusion,
    })
    table = pd.DataFrame(rows)
    table.to_csv(destination, index=False, encoding="utf-8-sig")
    return {
        "artifact": destination.name,
        "source": "结果/04_正式测试_封存/test_summary.csv + test_quarter_metrics.csv + bootstrap_ci.csv + test_open_record.json",
        "rows": len(table),
        "source_sha256": ";".join([sha256_file(summary_path), sha256_file(quarter_path), sha256_file(ci_path), sha256_file(test_dir / "test_open_record.json")]),
        "output_sha256": sha256_file(destination),
    }


def append_numeric_csv(
    index_rows: list[dict[str, Any]],
    root: Path,
    source: Path,
    stage: str,
    default_split: str,
    identity_columns: set[str],
    method_column: str | None = None,
    method_override: str | None = None,
    metric_prefix_column: str | None = None,
) -> None:
    if not source.exists():
        return
    frame = pd.read_csv(source)
    source_relative = str(source.relative_to(root))
    source_hash = sha256_file(source)
    for row_index, row in frame.iterrows():
        split = str(row.get("split", default_split))
        quarter = str(row.get("quarter", "all"))
        method = method_override or (str(row.get(method_column, "all")) if method_column else "all")
        prefix = str(row.get(metric_prefix_column, "")) if metric_prefix_column else ""
        for column in frame.columns:
            if column in identity_columns:
                continue
            numeric = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
            if pd.isna(numeric):
                continue
            metric = f"{prefix}.{column}" if prefix else str(column)
            source_row = int(row_index) + 2
            unique_key = "|".join([stage, split, quarter, method, metric, Path(source_relative).name, str(source_row)])
            index_rows.append({
                "unique_key": unique_key,
                "stage": stage,
                "split": split,
                "quarter": quarter,
                "method_or_comparison": method,
                "metric": metric,
                "value": numeric,
                "source_file": source_relative,
                "source_row": source_row,
                "source_column": column,
                "source_sha256": source_hash,
            })


def build_number_index(root: Path, record: dict[str, Any] | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    append_numeric_csv(rows, root, root / "结果/01_数据审计/样本排除流.csv", "data_flow", "all", {"step"}, method_column="step")
    append_numeric_csv(rows, root, root / "结果/01_数据审计/时间切分审计.csv", "split_audit", "all", {"split", "quarter"})
    append_numeric_csv(rows, root, root / "结果/01_数据审计/成熟度审计.csv", "maturity_audit", "all", {"split", "quarter", "maturity_boundary"})
    append_numeric_csv(rows, root, root / "结果/02_画像/画像汇总.csv", "profile_summary", "all", {"split", "quarter"})
    append_numeric_csv(rows, root, root / "结果/03_验证/validation_quarter_metrics.csv", "validation_quarter", "validation", {"quarter", "method"}, method_column="method")
    append_numeric_csv(rows, root, root / "结果/03_验证/validation_summary.csv", "validation_summary", "validation", {"method"}, method_column="method")
    if record is not None:
        test_dir = root / "结果/04_正式测试_封存"
        append_numeric_csv(rows, root, test_dir / "test_quarter_metrics.csv", "test_quarter", "test", {"quarter", "method"}, method_column="method")
        append_numeric_csv(rows, root, test_dir / "test_summary.csv", "test_summary", "test", {"method"}, method_column="method")
        append_numeric_csv(rows, root, test_dir / "bootstrap_ci.csv", "test_bootstrap", "test", {"metric"}, method_override="M2-M1", metric_prefix_column="metric")
        append_numeric_csv(rows, root, test_dir / "test_maturity_by_quarter.csv", "test_maturity", "test", {"quarter", "maturity_boundary"})
        source = test_dir / "test_open_record.json"
        source_hash = sha256_file(source)
        for key, value in record["pre_registered_criteria"].items():
            if not isinstance(value, (bool, int, float)):
                continue
            rows.append({
                "unique_key": f"test_criteria|test|all|M2-M1|{key}|test_open_record.json|json",
                "stage": "test_criteria",
                "split": "test",
                "quarter": "all",
                "method_or_comparison": "M2-M1",
                "metric": key,
                "value": str(value).lower() if isinstance(value, bool) else value,
                "source_file": str(source.relative_to(root)),
                "source_row": "$.pre_registered_criteria",
                "source_column": key,
                "source_sha256": source_hash,
            })
    result = pd.DataFrame(rows, columns=INDEX_COLUMNS)
    if result.empty:
        raise RuntimeError("没有可写入结果数字索引的正式数值。")
    if result["unique_key"].duplicated().any():
        duplicates = result.loc[result["unique_key"].duplicated(keep=False), "unique_key"].tolist()[:10]
        raise RuntimeError(f"结果数字索引键重复: {duplicates}")
    return result


def main(root: Path) -> None:
    root = root.resolve()
    ensure_dirs(root)
    out = root / "结果/05_论文图表"
    record = verify_frozen_and_opened_sources(root)
    created: list[dict[str, Any]] = []
    mapping = [
        (root / "结果/01_数据审计/样本排除流.csv", out / "表1_样本排除流.csv"),
        (root / "数据/02_分析数据/feature_dictionary.csv", out / "表2_画像指标说明.csv"),
        (root / "结果/03_验证/validation_summary.csv", out / "表3_Validation方法比较.csv"),
    ]
    for source, destination in mapping:
        if not source.exists():
            raise FileNotFoundError(f"缺少论文表来源: {source}")
        item = copy_table(source, destination)
        item["source"] = str(source.relative_to(root))
        created.append(item)

    figure_mapping = [
        (root / "结果/02_画像/图1_动态画像研究框架.svg", out / "图1_动态画像研究框架.svg"),
        (root / "结果/01_数据审计/图2_样本筛选流程.svg", out / "图2_样本筛选流程.svg"),
        (root / "结果/02_画像/图3_匿名画像时间轨迹.svg", out / "图3_匿名画像时间轨迹.svg"),
    ]
    for source, destination in figure_mapping:
        if not source.exists():
            raise FileNotFoundError(f"缺少论文图来源: {source}")
        destination.write_bytes(source.read_bytes())
        created.append({
            "artifact": destination.name,
            "source": str(source.relative_to(root)),
            "rows": None,
            "source_sha256": sha256_file(source),
            "output_sha256": sha256_file(destination),
        })

    if record is not None:
        table_path = out / "表4_Test方法比较.csv"
        created.append(build_test_table(root, record, table_path))
        test_quarters = root / "结果/04_正式测试_封存/test_quarter_metrics.csv"
        plot_data = pd.read_csv(test_quarters)
        plot_path = out / "图4_Test季度方法比较_作图数据.csv"
        plot_data.to_csv(plot_path, index=False, encoding="utf-8-sig")
        pivot = plot_data.pivot(index="quarter", columns="method", values="ap")
        ax = pivot.plot(marker="o", figsize=(8, 4.5))
        ax.set_xlabel("Quarter")
        ax.set_ylabel("Average precision")
        ax.grid(alpha=0.25)
        plt.tight_layout()
        svg_path = out / "图4_Test季度方法比较.svg"
        plt.savefig(svg_path, format="svg")
        plt.close()
        created.extend([
            {"artifact": plot_path.name, "source": str(test_quarters.relative_to(root)), "rows": len(plot_data), "source_sha256": sha256_file(test_quarters), "output_sha256": sha256_file(plot_path)},
            {"artifact": svg_path.name, "source": plot_path.name, "rows": None, "source_sha256": sha256_file(plot_path), "output_sha256": sha256_file(svg_path)},
        ])

    number_index = build_number_index(root, record)
    number_index_path = out / "结果数字索引.csv"
    number_index.to_csv(number_index_path, index=False, encoding="utf-8-sig")
    created.append({"artifact": number_index_path.name, "source": "冻结结果长表索引", "rows": len(number_index), "source_sha256": None, "output_sha256": sha256_file(number_index_path)})
    pd.DataFrame(created).to_csv(out / "表图来源清单.csv", index=False, encoding="utf-8-sig")

    if record is None:
        print("05完成: 已生成开封前表1—表3和数字索引；Test未开封，未生成任何Test数值。")
    else:
        print("05完成: 已核验冻结链和Test结果SHA-256，并生成含差值、区间、C1/C2的正式表4与数字索引。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    try:
        main(args.root)
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)
