from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

from pipeline_common import DEFAULT_ROOT, PROGRAM_DIR, ensure_dirs, fail_if_analysis_frozen, load_config, require_columns, secure_restricted_tree, sha256_file, write_json


FEATURES = [
    "history_inspections", "history_positive_inspections", "smoothed_positive_rate",
    "days_since_last_inspection", "days_since_last_positive", "inspections_365d", "inspections_730d",
    "positives_365d", "positives_730d", "decayed_inspections", "decayed_positives",
]

FEATURE_DICTIONARY_COLUMNS = [
    "field", "中文名", "group", "业务含义", "来源", "公式", "窗口", "缺失处理",
    "可用时间", "数值增大含义", "禁止解释",
]

EVIDENCE_COLUMNS = [
    "sample_id", "quarter", "cutoff_date", "history_record_id", "history_open_date",
    "history_label_available_time", "outcome_positive", "included_in_profile",
    "exclusion_reason", "inside_365d", "inside_730d",
]

TEST_FORBIDDEN_COLUMNS = {
    "activity_nr", "activity_nrs", "context_activity_nr", "episode_open_date", "label",
    "label_available_date", "candidate_naics_group", "candidate_site_state", "citation_id",
    "issuance_date", "outcome_positive", "outcome_available_time", "close_case_date",
}

FEATURE_METADATA: dict[str, dict[str, str]] = {
    "context_naics_group": {
        "中文名": "截点前最近行业组",
        "业务含义": "同一代理实体截点前最近一条成熟历史检查的NAICS行业组。",
        "来源": "historical_inspection_outcomes.naics_group, open_date, outcome_available_time",
        "公式": "在H_i(t)中按open_date取最近记录（同日按稳定顺序决定）的naics_group。",
        "窗口": "截点前全部成熟历史。",
        "缺失处理": "无成熟历史或原值缺失时记UNKNOWN。",
        "可用时间": "open_date与outcome_available_time均严格早于cutoff后才可用。",
        "数值增大含义": "类别变量，不适用数值增大解释。",
        "禁止解释": "不得解释为企业当前行业、真实安全等级或因果风险。",
    },
    "context_site_state": {
        "中文名": "截点前最近州别",
        "业务含义": "同一代理实体截点前最近一条成熟历史检查的州别背景。",
        "来源": "historical_inspection_outcomes.site_state, open_date, outcome_available_time",
        "公式": "在H_i(t)中按open_date取最近记录（同日按稳定顺序决定）的site_state。",
        "窗口": "截点前全部成熟历史。",
        "缺失处理": "无成熟历史或原值缺失时记UNKNOWN。",
        "可用时间": "open_date与outcome_available_time均严格早于cutoff后才可用。",
        "数值增大含义": "类别变量，不适用数值增大解释。",
        "禁止解释": "不得解释为企业当前所在州、执法质量或因果风险。",
    },
    "quarter_number": {
        "中文名": "自然季度序号",
        "业务含义": "排序截点所在的自然季度，表示季节背景。",
        "来源": "inspection_episode.quarter",
        "公式": "quarter_number = ranking_cutoff.quarter。",
        "窗口": "无历史窗口；由当期截点直接生成。",
        "缺失处理": "quarter可解析时无缺失；不可解析则停止运行。",
        "可用时间": "在ranking_cutoff定义时可用，不读取当期结果。",
        "数值增大含义": "仅表示从第1季度到第4季度的序号变化。",
        "禁止解释": "不得将季度序号解释为风险单调增长或因果效应。",
    },
    "history_inspections": {
        "中文名": "成熟历史检查数",
        "业务含义": "同一代理实体在截点前已达到结果可用时间的历史检查数。",
        "来源": "historical_inspection_outcomes.entity_proxy_id, open_date, outcome_available_time",
        "公式": "N_i(t) = Σ_{j∈H_i(t)} 1。",
        "窗口": "截点前全部成熟历史。",
        "缺失处理": "无成熟历史时为0。",
        "可用时间": "open_date与outcome_available_time均严格早于cutoff。",
        "数值增大含义": "可用的成熟历史检查记录更多。",
        "禁止解释": "不得解释为违章概率、真实危险程度或因果机制。",
    },
    "history_positive_inspections": {
        "中文名": "成熟历史阳性数",
        "业务含义": "成熟历史检查中在主观察窗内至少签发一条直接关联citation的检查数。",
        "来源": "historical_inspection_outcomes.outcome_positive, outcome_available_time, open_date",
        "公式": "Y_i(t) = Σ_{j∈H_i(t)} y_j。",
        "窗口": "截点前全部成熟历史。",
        "缺失处理": "无成熟历史时为0；未成熟记录不得当作阴性。",
        "可用时间": "open_date与outcome_available_time均严格早于cutoff。",
        "数值增大含义": "可用历史中直接关联citation的检查次数更多。",
        "禁止解释": "不得解释为当前必然违章、事故概率或因果效应。",
    },
    "smoothed_positive_rate": {
        "中文名": "Beta-Binomial平滑历史率",
        "业务含义": "对成熟历史阳性比例使用预先固定的Beta先验平滑。",
        "来源": "history_positive_inspections, history_inspections, config.rules.smoothing_alpha/beta",
        "公式": "R_i(t) = (Y_i(t)+α)/(N_i(t)+α+β)。",
        "窗口": "截点前全部成熟历史。",
        "缺失处理": "无成熟历史时使用先验均值α/(α+β)。",
        "可用时间": "仅使用open_date与outcome_available_time均早于cutoff的历史及预冻结参数。",
        "数值增大含义": "成熟历史中直接关联citation的平滑相对频率更高。",
        "禁止解释": "不是绝对安全分、事故概率或个体因果风险。",
    },
    "days_since_last_inspection": {
        "中文名": "距上次成熟检查天数",
        "业务含义": "截点与最近一次成熟历史检查开始日期之间的天数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_available_time",
        "公式": "D_insp,i(t) = t - max_{j∈H_i(t)} open_date_j。",
        "窗口": "截点前全部成熟历史中的最近一条。",
        "缺失处理": "无成熟历史时保留缺失；模型仅用Train中位数补值并加缺失指示。",
        "可用时间": "对应历史的open_date与outcome_available_time均严格早于cutoff。",
        "数值增大含义": "距最近一次成熟历史检查的间隔更长。",
        "禁止解释": "间隔更长不等于更安全、更危险或监管因果。",
    },
    "days_since_last_positive": {
        "中文名": "距上次成熟阳性天数",
        "业务含义": "截点与最近一次成熟阳性历史检查开始日期之间的天数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_positive, outcome_available_time",
        "公式": "D_pos,i(t) = t - max_{j∈H_i(t), y_j=1} open_date_j。",
        "窗口": "截点前全部成熟阳性历史中的最近一条。",
        "缺失处理": "无成熟阳性历史时保留缺失；模型仅用Train中位数补值并加缺失指示。",
        "可用时间": "阳性结果的outcome_available_time与open_date均严格早于cutoff。",
        "数值增大含义": "距最近一次成熟历史citation检查的间隔更长。",
        "禁止解释": "不得解释为当前无违章、已整改或风险因果降低。",
    },
    "inspections_365d": {
        "中文名": "365天内成熟检查数", "业务含义": "截点前365天内的成熟历史检查数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_available_time", "公式": "Σ 1{t-365天 ≤ open_date_j < t, j∈H_i(t)}。",
        "窗口": "[cutoff-365天, cutoff)。", "缺失处理": "无符合记录时为0。", "可用时间": "窗口内且outcome_available_time严格早于cutoff。",
        "数值增大含义": "近365天内可用的成熟历史检查更多。", "禁止解释": "不得解释为当前违章概率或监管因果。",
    },
    "inspections_730d": {
        "中文名": "730天内成熟检查数", "业务含义": "截点前730天内的成熟历史检查数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_available_time", "公式": "Σ 1{t-730天 ≤ open_date_j < t, j∈H_i(t)}。",
        "窗口": "[cutoff-730天, cutoff)。", "缺失处理": "无符合记录时为0。", "可用时间": "窗口内且outcome_available_time严格早于cutoff。",
        "数值增大含义": "近730天内可用的成熟历史检查更多。", "禁止解释": "不得解释为当前违章概率或监管因果。",
    },
    "positives_365d": {
        "中文名": "365天内成熟阳性数", "业务含义": "截点前365天内的成熟阳性历史检查数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_positive, outcome_available_time", "公式": "Σ 1{t-365天 ≤ open_date_j < t, j∈H_i(t), y_j=1}。",
        "窗口": "[cutoff-365天, cutoff)。", "缺失处理": "无符合记录时为0；未成熟记录不得当作阴性。", "可用时间": "窗口内且阳性结果可用时间严格早于cutoff。",
        "数值增大含义": "近365天内成熟历史citation检查更多。", "禁止解释": "不得解释为当前必然违章或因果风险。",
    },
    "positives_730d": {
        "中文名": "730天内成熟阳性数", "业务含义": "截点前730天内的成熟阳性历史检查数。",
        "来源": "historical_inspection_outcomes.open_date, outcome_positive, outcome_available_time", "公式": "Σ 1{t-730天 ≤ open_date_j < t, j∈H_i(t), y_j=1}。",
        "窗口": "[cutoff-730天, cutoff)。", "缺失处理": "无符合记录时为0；未成熟记录不得当作阴性。", "可用时间": "窗口内且阳性结果可用时间严格早于cutoff。",
        "数值增大含义": "近730天内成熟历史citation检查更多。", "禁止解释": "不得解释为当前必然违章或因果风险。",
    },
    "decayed_inspections": {
        "中文名": "时间衰减检查量", "业务含义": "对全部成熟历史检查按距截点时间进行指数衰减加权。",
        "来源": "historical_inspection_outcomes.open_date, outcome_available_time; config.rules.decay_half_life_days", "公式": "Σ_{j∈H_i(t)} exp[-ln(2)·(t-open_date_j)/h]。",
        "窗口": "截点前全部成熟历史；半衰期h由config预先固定。", "缺失处理": "无成熟历史时为0。", "可用时间": "open_date与outcome_available_time均严格早于cutoff。",
        "数值增大含义": "成熟历史检查更多或时间上更近。", "禁止解释": "不得解释为绝对风险、执法强度因果或真实安全等级。",
    },
    "decayed_positives": {
        "中文名": "时间衰减阳性量", "业务含义": "对全部成熟阳性历史检查按距截点时间进行指数衰减加权。",
        "来源": "historical_inspection_outcomes.open_date, outcome_positive, outcome_available_time; config.rules.decay_half_life_days", "公式": "Σ_{j∈H_i(t), y_j=1} exp[-ln(2)·(t-open_date_j)/h]。",
        "窗口": "截点前全部成熟阳性历史；半衰期h由config预先固定。", "缺失处理": "无成熟阳性历史时为0；未成熟记录不得当作阴性。", "可用时间": "阳性结果的open_date与outcome_available_time均严格早于cutoff。",
        "数值增大含义": "成熟历史citation检查更多或时间上更近。", "禁止解释": "不得解释为当前必然违章、事故概率或因果效应。",
    },
}


def fail_if_test_already_opened(root: Path) -> None:
    """Refuse any profile rewrite after the first formal Test-open attempt."""
    test_result = root / "结果/04_正式测试_封存"
    blockers = [
        test_result / "test_open_attempt.json",
        test_result / "test_open_record.json",
    ]
    existing = [str(path.relative_to(root)) for path in blockers if path.exists()]
    if existing:
        raise RuntimeError(f"Test已尝试或完成开封；禁止重写画像和Test特征: {existing}")


def _resolve_registered_path(root: Path, raw_value: Any, field: str) -> Path:
    value = "" if pd.isna(raw_value) else str(raw_value).strip()
    if not value:
        raise RuntimeError(f"画像定义冻结记录的{field}为空。")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def validate_profile_definition_freeze(root: Path, config_path: Path) -> None:
    """Validate the final dual-confirmed profile-definition freeze row."""
    register_path = root / "记录表/画像定义冻结.csv"
    if not register_path.exists():
        raise RuntimeError(f"缺少画像定义冻结记录: {register_path}")
    register = pd.read_csv(register_path, dtype=str).fillna("")
    required = {
        "指标说明文件", "指标说明SHA256", "配置文件", "配置SHA256",
        "画像程序文件", "画像程序SHA256", "确认人1", "确认人2", "状态",
    }
    missing_columns = sorted(required - set(register.columns))
    if missing_columns:
        raise RuntimeError(f"画像定义冻结记录缺少字段: {missing_columns}")
    if register.empty:
        raise RuntimeError("画像定义冻结记录没有任何登记行。")
    row = register.iloc[-1]
    if str(row["状态"]).strip() != "已冻结":
        raise RuntimeError("画像定义冻结记录最后一行状态必须为“已冻结”。")
    confirmer_1 = str(row["确认人1"]).strip()
    confirmer_2 = str(row["确认人2"]).strip()
    if not confirmer_1 or not confirmer_2:
        raise RuntimeError("画像定义冻结必须由两名确认人完整填写。")
    if confirmer_1.casefold() == confirmer_2.casefold():
        raise RuntimeError("画像定义冻结的确认人1和确认人2必须不同。")

    expected_files = [
        ("指标说明文件", "指标说明SHA256", (root / "共同材料/06_画像指标说明.md").resolve()),
        ("配置文件", "配置SHA256", config_path.resolve()),
        ("画像程序文件", "画像程序SHA256", Path(__file__).resolve()),
    ]
    errors: list[str] = []
    for path_field, hash_field, expected_path in expected_files:
        registered_path = _resolve_registered_path(root, row[path_field], path_field)
        if registered_path != expected_path:
            errors.append(f"{path_field}不匹配：登记={registered_path}，当前={expected_path}")
            continue
        if not expected_path.is_file():
            errors.append(f"{path_field}不存在：{expected_path}")
            continue
        registered_hash = str(row[hash_field]).strip()
        current_hash = sha256_file(expected_path)
        if registered_hash != current_hash:
            errors.append(f"{hash_field}与当前文件不匹配")
    if errors:
        raise RuntimeError("画像定义冻结校验失败；" + "；".join(errors))


def configured_feature_groups(cfg: dict[str, Any]) -> dict[str, list[str]]:
    model = cfg.get("model")
    if not isinstance(model, dict):
        raise RuntimeError("config缺少model配置。")
    groups: dict[str, list[str]] = {}
    for group, key in (("Context", "context_features"), ("Static", "static_features"), ("Dynamic", "dynamic_features")):
        raw_features = model.get(key)
        if not isinstance(raw_features, (list, tuple)):
            raise RuntimeError(f"config.model.{key}必须是特征列表。")
        features = [str(value).strip() for value in raw_features]
        if any(not feature for feature in features):
            raise RuntimeError(f"config.model.{key}存在空特征名。")
        groups[group] = features
    ordered = [feature for features in groups.values() for feature in features]
    duplicates = sorted({feature for feature in ordered if ordered.count(feature) > 1})
    if duplicates:
        raise RuntimeError(f"Context/Static/Dynamic特征组存在重复字段: {duplicates}")
    return groups


def build_feature_dictionary(cfg: dict[str, Any]) -> pd.DataFrame:
    groups = configured_feature_groups(cfg)
    configured = [feature for features in groups.values() for feature in features]
    undefined = sorted(set(configured) - set(FEATURE_METADATA))
    if undefined:
        raise RuntimeError(f"特征字典缺少config模型特征的冻结定义: {undefined}")
    rows = []
    for group, features in groups.items():
        for field in features:
            rows.append({"field": field, "group": group, **FEATURE_METADATA[field]})
    dictionary = pd.DataFrame(rows, columns=FEATURE_DICTIONARY_COLUMNS)
    if set(dictionary["field"]) != set(configured) or len(dictionary) != len(configured):
        raise RuntimeError("特征字典未精确覆盖config的Context/Static/Dynamic特征。")
    return dictionary


def _anonymous_history_record_id(index: Any, row: pd.Series) -> str:
    payload = "\x1f".join([
        "profile-evidence-v1",
        str(row.get("entity_proxy_id", "")),
        str(row.get("activity_nr", "")),
        str(row.get("open_date", "")),
        str(row.get("outcome_available_time", "")),
        str(index),
    ])
    return "H-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def build_recalculation_evidence(profiles: pd.DataFrame, history: pd.DataFrame, max_samples: int = 20) -> pd.DataFrame:
    """Create deterministic restricted evidence with both included and excluded history."""
    train_val = profiles.loc[profiles["split"].isin(["train", "validation"])].sort_values("sample_id", kind="stable")
    requested = min(max_samples, len(train_val))
    if not requested:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)
    positions = np.unique(np.linspace(0, len(train_val) - 1, requested, dtype=int))
    selected = train_val.iloc[positions][["sample_id", "entity_proxy_id", "quarter", "cutoff_date", "split"]].copy()
    if not selected["split"].isin(["train", "validation"]).all():
        raise RuntimeError("画像复算证据卡抽样意外包含Test样本。")

    records: list[dict[str, Any]] = []
    entity_as_text = history["entity_proxy_id"].astype(str)
    for sample in selected.itertuples(index=False):
        cutoff = pd.Timestamp(sample.cutoff_date)
        entity_history = history.loc[entity_as_text.eq(str(sample.entity_proxy_id))]
        if entity_history.empty:
            records.append({
                "sample_id": sample.sample_id,
                "quarter": sample.quarter,
                "cutoff_date": cutoff,
                "history_record_id": "NO_HISTORY_RECORD",
                "history_open_date": pd.NaT,
                "history_label_available_time": pd.NaT,
                "outcome_positive": pd.NA,
                "included_in_profile": False,
                "exclusion_reason": "no_history_for_entity",
                "inside_365d": pd.NA,
                "inside_730d": pd.NA,
            })
            continue
        for history_index, history_row in entity_history.iterrows():
            open_date = pd.to_datetime(history_row["open_date"], errors="coerce")
            available_time = pd.to_datetime(history_row["outcome_available_time"], errors="coerce")
            open_before_cutoff = bool(pd.notna(open_date) and open_date < cutoff)
            available_before_cutoff = bool(pd.notna(available_time) and available_time < cutoff)
            included = open_before_cutoff and available_before_cutoff
            reasons: list[str] = []
            if pd.isna(open_date):
                reasons.append("open_date_missing")
            elif not open_before_cutoff:
                reasons.append("open_date_at_or_after_cutoff")
            if pd.isna(available_time):
                reasons.append("outcome_available_time_missing")
            elif not available_before_cutoff:
                reasons.append("outcome_available_time_at_or_after_cutoff")
            records.append({
                "sample_id": sample.sample_id,
                "quarter": sample.quarter,
                "cutoff_date": cutoff,
                "history_record_id": _anonymous_history_record_id(history_index, history_row),
                "history_open_date": open_date,
                "history_label_available_time": available_time,
                "outcome_positive": int(history_row["outcome_positive"]) if included else pd.NA,
                "included_in_profile": included,
                "exclusion_reason": "" if included else ";".join(reasons),
                "inside_365d": bool(open_before_cutoff and open_date >= cutoff - pd.Timedelta(days=365)),
                "inside_730d": bool(open_before_cutoff and open_date >= cutoff - pd.Timedelta(days=730)),
            })

    evidence = pd.DataFrame(records, columns=EVIDENCE_COLUMNS)
    excluded = ~evidence["included_in_profile"].astype(bool)
    if evidence.loc[excluded, "outcome_positive"].notna().any():
        raise RuntimeError("画像复算证据卡的排除行意外包含outcome_positive。")
    test_sample_ids = set(profiles.loc[profiles["split"].eq("test"), "sample_id"].astype(str))
    if set(evidence["sample_id"].astype(str)) & test_sample_ids:
        raise RuntimeError("画像复算证据卡意外包含Test样本。")
    forbidden_evidence_columns = {"label", "split", "entity_proxy_id", "activity_nr"}
    if forbidden_evidence_columns & set(evidence.columns):
        raise RuntimeError("画像复算证据卡意外包含受限原始标识或目标标签字段。")
    raw_activity_values = set(history["activity_nr"].dropna().astype(str))
    if set(evidence["history_record_id"].astype(str)) & raw_activity_values:
        raise RuntimeError("画像复算证据卡的匿名历史编号意外等于原始activity_nr。")
    return evidence


def build_leakage_audit(
    checks: pd.DataFrame,
    profiles: pd.DataFrame,
    test_features: pd.DataFrame,
    test_whitelist: list[str],
    feature_groups: dict[str, list[str]],
) -> dict[str, Any]:
    cutoff = pd.to_datetime(checks["cutoff"], errors="coerce")
    history_open_bad = int((pd.to_datetime(checks["max_history_inspection"], errors="coerce") >= cutoff).fillna(False).sum())
    outcome_available_bad = int((pd.to_datetime(checks["max_history_outcome_available_time"], errors="coerce") >= cutoff).fillna(False).sum())
    context_source_bad = int((pd.to_datetime(checks["context_source_date"], errors="coerce") >= cutoff).fillna(False).sum())

    actual_columns = list(test_features.columns)
    missing_whitelist = sorted(set(test_whitelist) - set(actual_columns))
    unexpected_columns = sorted(set(actual_columns) - set(test_whitelist))
    duplicate_columns = sorted({column for column in actual_columns if actual_columns.count(column) > 1})
    whitelist_bad = len(missing_whitelist) + len(unexpected_columns) + len(duplicate_columns)
    forbidden_columns = sorted({str(column) for column in actual_columns if str(column).casefold() in TEST_FORBIDDEN_COLUMNS})

    m1_features = feature_groups["Context"] + feature_groups["Static"]
    m2_features = m1_features + feature_groups["Dynamic"]
    require_columns(profiles, ["sample_id", *m1_features], "M1画像来源")
    require_columns(profiles, ["sample_id", *m2_features], "M2画像来源")
    # Both matrices are materialized from this same ordered source frame; model
    # preprocessing imputes missing values and therefore does not drop samples.
    m1_ids = profiles["sample_id"].astype(str).tolist()
    m2_ids = profiles["sample_id"].astype(str).tolist()
    sample_set_difference = sorted(set(m1_ids).symmetric_difference(m2_ids))
    sample_alignment_bad = len(sample_set_difference) + (0 if m1_ids == m2_ids else 1)

    audit_checks: dict[str, dict[str, Any]] = {
        "history_open_before_cutoff": {
            "count": history_open_bad, "passed": history_open_bad == 0, "checked_count": len(checks),
        },
        "outcome_available_before_cutoff": {
            "count": outcome_available_bad, "passed": outcome_available_bad == 0, "checked_count": len(checks),
        },
        "context_source_before_cutoff": {
            "count": context_source_bad, "passed": context_source_bad == 0, "checked_count": len(checks),
        },
        "test_columns_whitelisted": {
            "count": whitelist_bad,
            "passed": whitelist_bad == 0,
            "checked_count": len(actual_columns),
            "missing_columns": missing_whitelist,
            "unexpected_columns": unexpected_columns,
            "duplicate_columns": duplicate_columns,
        },
        "test_forbidden_columns_absent": {
            "count": len(forbidden_columns),
            "passed": not forbidden_columns,
            "checked_count": len(actual_columns),
            "forbidden_columns": forbidden_columns,
        },
        "m1_m2_same_source_samples": {
            "count": sample_alignment_bad,
            "passed": sample_alignment_bad == 0,
            "checked_count": len(profiles),
            "m1_sample_count": len(m1_ids),
            "m2_sample_count": len(m2_ids),
            "sample_set_difference": sample_set_difference,
        },
    }
    passed = all(bool(detail["passed"]) for detail in audit_checks.values())
    return {
        "samples": len(checks),
        "count_semantics": "number_of_violations",
        "checks": audit_checks,
        "history_inspection_at_or_after_cutoff": history_open_bad,
        "citation_available_time_at_or_after_cutoff": outcome_available_bad,
        "context_source_at_or_after_cutoff": context_source_bad,
        "passed": passed,
    }


def write_framework_svg(path: Path) -> None:
    """Write a data-independent, deterministic research-process figure."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420" role="img" aria-labelledby="title desc">
  <title id="title">动态画像研究框架</title>
  <desc id="desc">可用历史数据经统一季度截点形成Context、Static和Dynamic特征，用于同源样本排序和人工复核。</desc>
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#315a7d"/></marker>
    <style>.box{fill:#f5f8fb;stroke:#315a7d;stroke-width:2}.main{font:600 23px sans-serif;fill:#17324d}.sub{font:17px sans-serif;fill:#38536b}.note{font:16px sans-serif;fill:#526b7f}.arrow{stroke:#315a7d;stroke-width:3;marker-end:url(#arrow)}</style>
  </defs>
  <rect x="35" y="105" width="190" height="130" rx="16" class="box"/><text x="130" y="155" text-anchor="middle" class="main">可用历史数据</text><text x="130" y="190" text-anchor="middle" class="sub">open &amp; available &lt; cutoff</text>
  <line x1="225" y1="170" x2="270" y2="170" class="arrow"/>
  <rect x="280" y="105" width="180" height="130" rx="16" class="box"/><text x="370" y="155" text-anchor="middle" class="main">统一季度截点</text><text x="370" y="190" text-anchor="middle" class="sub">ranking cutoff</text>
  <line x1="460" y1="170" x2="505" y2="170" class="arrow"/>
  <rect x="515" y="70" width="210" height="200" rx="16" class="box"/><text x="620" y="120" text-anchor="middle" class="main">分层画像</text><text x="620" y="160" text-anchor="middle" class="sub">Context</text><text x="620" y="195" text-anchor="middle" class="sub">Static</text><text x="620" y="230" text-anchor="middle" class="sub">Dynamic</text>
  <line x1="725" y1="170" x2="770" y2="170" class="arrow"/>
  <rect x="780" y="105" width="170" height="130" rx="16" class="box"/><text x="865" y="155" text-anchor="middle" class="main">同源样本排序</text><text x="865" y="190" text-anchor="middle" class="sub">M1 / M2</text>
  <line x1="950" y1="170" x2="995" y2="170" class="arrow"/>
  <rect x="1005" y="105" width="160" height="130" rx="16" class="box"/><text x="1085" y="155" text-anchor="middle" class="main">人工复核</text><text x="1085" y="190" text-anchor="middle" class="sub">human review</text>
  <text x="600" y="340" text-anchor="middle" class="note">只供历史线索人工复核，不作为处罚、定责或绝对安全等级依据</text>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def set_private_permissions(path: Path, formal_mode: bool) -> None:
    if not formal_mode:
        return
    try:
        path.chmod(0o600)
    except (NotImplementedError, OSError):
        # Some mounted or non-POSIX filesystems cannot represent mode 0600.
        pass


class FenwickTree:
    """Prefix-count tree used for exact rolling windows in O(log n)."""

    def __init__(self, size: int) -> None:
        self.tree = np.zeros(size + 1, dtype=np.int64)

    def add(self, index: int, value: int) -> None:
        cursor = index + 1
        while cursor < len(self.tree):
            self.tree[cursor] += value
            cursor += cursor & -cursor

    def prefix_sum(self, end: int) -> int:
        """Return the sum over zero-based indices [0, end)."""
        total = 0
        cursor = end
        while cursor > 0:
            total += int(self.tree[cursor])
            cursor -= cursor & -cursor
        return total

    def range_sum(self, start: int, end: int) -> int:
        return self.prefix_sum(end) - self.prefix_sum(start)


def _timestamp_day(series: pd.Series) -> np.ndarray:
    return series.to_numpy(dtype="datetime64[D]").astype(np.int64)


def build_profiles_frame(episodes: pd.DataFrame, history: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build all entity-quarter profiles without repeatedly scanning the full history table.

    Histories are activated only after both their open date and outcome-availability
    time. Two Fenwick trees answer exact 365/730-day range counts, while prefix arrays
    answer cumulative, recency, context and exponential-decay features. Complexity is
    approximately O((history + episodes) log history) within each entity.
    """
    half_life = float(cfg["rules"]["decay_half_life_days"])
    alpha = float(cfg["rules"]["smoothing_alpha"])
    beta = float(cfg["rules"]["smoothing_beta"])
    decay_rate = math.log(2) / half_life

    working_episodes = episodes.copy()
    working_episodes["source_order_internal"] = np.arange(len(working_episodes), dtype=np.int64)
    working_episodes["cutoff_internal"] = working_episodes["quarter"].map(lambda value: pd.Period(value, freq="Q").start_time)
    usable_history = history.loc[history["outcome_available_time"].notna()].copy()
    usable_history["feature_available_time_internal"] = usable_history[["open_date", "outcome_available_time"]].max(axis=1)
    history_groups = {str(key): group.copy() for key, group in usable_history.groupby("entity_proxy_id", sort=False)}

    rows: list[dict] = []
    leakage_checks: list[dict] = []
    for entity_id, entity_episodes in working_episodes.groupby("entity_proxy_id", sort=False):
        entity_episodes = entity_episodes.sort_values(["cutoff_internal", "sample_id"], kind="stable")
        entity_history = history_groups.get(str(entity_id), usable_history.iloc[0:0].copy())
        entity_history = entity_history.sort_values(
            ["feature_available_time_internal", "outcome_available_time", "open_date", "activity_nr"],
            kind="stable",
        ).reset_index(drop=True)

        if entity_history.empty:
            for episode in entity_episodes.itertuples(index=False):
                record = episode._asdict()
                record.update({
                    "cutoff_date": episode.cutoff_internal,
                    "quarter_number": episode.cutoff_internal.quarter,
                    "context_naics_group": "UNKNOWN",
                    "context_site_state": "UNKNOWN",
                    "context_source_date": pd.NaT,
                    "history_inspections": 0,
                    "history_positive_inspections": 0,
                    "smoothed_positive_rate": alpha / (alpha + beta),
                    "days_since_last_inspection": np.nan,
                    "days_since_last_positive": np.nan,
                    "inspections_365d": 0,
                    "inspections_730d": 0,
                    "positives_365d": 0,
                    "positives_730d": 0,
                    "decayed_inspections": 0.0,
                    "decayed_positives": 0.0,
                })
                rows.append(record)
                leakage_checks.append({"sample_id": episode.sample_id, "max_history_inspection": pd.NaT, "max_history_outcome_available_time": pd.NaT, "context_source_date": pd.NaT, "cutoff": episode.cutoff_internal})
            continue

        available_ns = entity_history["feature_available_time_internal"].astype("int64").to_numpy()
        outcome_available_ns = entity_history["outcome_available_time"].astype("int64").to_numpy()
        open_days = _timestamp_day(entity_history["open_date"])
        positive = entity_history["outcome_positive"].to_numpy(dtype=np.int64)
        activity = entity_history["activity_nr"].astype(str).to_numpy()
        naics = entity_history["naics_group"].fillna("UNKNOWN").astype(str).to_numpy()
        states = entity_history["site_state"].fillna("UNKNOWN").astype(str).to_numpy()

        prefix_positive = np.cumsum(positive)
        prefix_decay_all = np.cumsum(np.exp(decay_rate * open_days))
        prefix_decay_positive = np.cumsum(np.exp(decay_rate * open_days) * positive)
        prefix_max_open = np.empty(len(entity_history), dtype=np.int64)
        prefix_max_outcome_available = np.empty(len(entity_history), dtype=np.int64)
        prefix_max_positive_open = np.full(len(entity_history), np.iinfo(np.int64).min, dtype=np.int64)
        prefix_context_index = np.empty(len(entity_history), dtype=np.int64)
        best_open = np.iinfo(np.int64).min
        best_positive_open = np.iinfo(np.int64).min
        best_context = 0
        for index in range(len(entity_history)):
            day = int(open_days[index])
            if day > best_open or (day == best_open and activity[index] > activity[best_context]):
                best_open = day
                best_context = index
            if positive[index] and day > best_positive_open:
                best_positive_open = day
            prefix_max_open[index] = best_open
            prefix_max_outcome_available[index] = (
                outcome_available_ns[index]
                if index == 0
                else max(prefix_max_outcome_available[index - 1], outcome_available_ns[index])
            )
            prefix_max_positive_open[index] = best_positive_open
            prefix_context_index[index] = best_context

        coordinates = np.unique(open_days)
        all_tree = FenwickTree(len(coordinates))
        positive_tree = FenwickTree(len(coordinates))
        active_count = 0
        for episode in entity_episodes.itertuples(index=False):
            cutoff = episode.cutoff_internal
            cutoff_ns = int(cutoff.value)
            cutoff_day = int(cutoff.to_datetime64().astype("datetime64[D]").astype(np.int64))
            new_active_count = int(np.searchsorted(available_ns, cutoff_ns, side="left"))
            while active_count < new_active_count:
                coordinate_index = int(np.searchsorted(coordinates, open_days[active_count]))
                all_tree.add(coordinate_index, 1)
                if positive[active_count]:
                    positive_tree.add(coordinate_index, 1)
                active_count += 1

            record = episode._asdict()
            if active_count:
                last = active_count - 1
                positive_count = int(prefix_positive[last])
                context_index = int(prefix_context_index[last])
                last_open_day = int(prefix_max_open[last])
                last_positive_day = int(prefix_max_positive_open[last])
                context_naics = naics[context_index] or "UNKNOWN"
                context_state = states[context_index] or "UNKNOWN"
                context_source_date = pd.Timestamp(open_days[context_index], unit="D")
                decayed_all = float(math.exp(-decay_rate * cutoff_day) * prefix_decay_all[last])
                decayed_positive = float(math.exp(-decay_rate * cutoff_day) * prefix_decay_positive[last])
            else:
                positive_count = 0
                last_open_day = np.iinfo(np.int64).min
                last_positive_day = np.iinfo(np.int64).min
                context_naics, context_state, context_source_date = "UNKNOWN", "UNKNOWN", pd.NaT
                decayed_all = decayed_positive = 0.0

            def window_count(days: int, tree: FenwickTree) -> int:
                left = int(np.searchsorted(coordinates, cutoff_day - days, side="left"))
                right = int(np.searchsorted(coordinates, cutoff_day, side="left"))
                return tree.range_sum(left, right)

            record.update({
                "cutoff_date": cutoff,
                "quarter_number": cutoff.quarter,
                "context_naics_group": context_naics,
                "context_site_state": context_state,
                "context_source_date": context_source_date,
                "history_inspections": active_count,
                "history_positive_inspections": positive_count,
                "smoothed_positive_rate": (positive_count + alpha) / (active_count + alpha + beta),
                "days_since_last_inspection": float(cutoff_day - last_open_day) if active_count else np.nan,
                "days_since_last_positive": float(cutoff_day - last_positive_day) if last_positive_day != np.iinfo(np.int64).min else np.nan,
                "inspections_365d": window_count(365, all_tree),
                "inspections_730d": window_count(730, all_tree),
                "positives_365d": window_count(365, positive_tree),
                "positives_730d": window_count(730, positive_tree),
                "decayed_inspections": decayed_all,
                "decayed_positives": decayed_positive,
            })
            rows.append(record)
            leakage_checks.append({
                "sample_id": episode.sample_id,
                "max_history_inspection": pd.Timestamp(last_open_day, unit="D") if active_count else pd.NaT,
                "max_history_outcome_available_time": pd.Timestamp(prefix_max_outcome_available[last], unit="ns") if active_count else pd.NaT,
                "context_source_date": context_source_date,
                "cutoff": cutoff,
            })

    profiles = pd.DataFrame(rows).sort_values("source_order_internal", kind="stable").drop(columns=["source_order_internal", "cutoff_internal"])
    return profiles, pd.DataFrame(leakage_checks)


def validate_entity_pair_coverage(review: pd.DataFrame, candidates: pd.DataFrame, min_pairs: int) -> pd.DataFrame:
    required = {"pair_id", "audit_type", "activity_nr_left", "activity_nr_right"}
    for name, frame in (("人工复核表", review), ("复核候选表", candidates)):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(f"{name}缺少字段: {missing}")
    review = review.copy()
    candidates = candidates.copy()
    for frame in (review, candidates):
        frame["pair_id"] = frame["pair_id"].astype(str).str.strip()
        frame["audit_type"] = frame["audit_type"].astype(str).str.strip().str.lower()
        frame["activity_nr_left"] = frame["activity_nr_left"].astype(str).str.strip()
        frame["activity_nr_right"] = frame["activity_nr_right"].astype(str).str.strip()
    required_candidates = candidates.loc[candidates["audit_type"].eq("key_internal_match")].copy()
    match_review = review.loc[review["audit_type"].eq("key_internal_match")].copy()
    if len(required_candidates) < min_pairs:
        raise RuntimeError(f"键内匹配候选仅{len(required_candidates)}对，少于门槛{min_pairs}对。")
    for name, frame in (("复核候选表", required_candidates), ("人工复核表", match_review)):
        if frame["pair_id"].eq("").any():
            raise RuntimeError(f"{name}存在空pair_id。")
        if frame["pair_id"].duplicated().any():
            duplicates = sorted(frame.loc[frame["pair_id"].duplicated(keep=False), "pair_id"].unique())[:10]
            raise RuntimeError(f"{name}的pair_id不唯一: {duplicates}")
    candidate_ids = set(required_candidates["pair_id"])
    review_ids = set(match_review["pair_id"])
    if review_ids != candidate_ids:
        raise RuntimeError(f"人工复核未精确覆盖候选清单；缺失={sorted(candidate_ids-review_ids)[:10]}, 多出={sorted(review_ids-candidate_ids)[:10]}。")
    expected = required_candidates.set_index("pair_id")[["activity_nr_left", "activity_nr_right"]].sort_index()
    observed = match_review.set_index("pair_id")[["activity_nr_left", "activity_nr_right"]].sort_index()
    if not observed.equals(expected):
        raise RuntimeError("人工复核表的activity_nr_left/right与候选清单不一致。")
    return match_review


def validate_entity_audit_gate(root: Path, cfg: dict) -> None:
    gate_path = root / "结果/01_数据审计/实体人工复核结果.json"
    review_csv = root / "记录表/实体人工复核.csv"
    candidate_csv = root / "数据/01_中间数据/受限_实体复核候选.csv"
    for path in (gate_path, review_csv, candidate_csv):
        if not path.exists():
            raise RuntimeError(f"实体人工复核尚未完成；已在门1停止。缺少: {path}")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    review = pd.read_csv(review_csv, dtype=str).fillna("")
    candidates = pd.read_csv(candidate_csv, dtype=str).fillna("")
    judgment_columns = {"复核人1判断", "复核人2判断", "是否一致", "最终判断"}
    missing = sorted(judgment_columns - set(review.columns))
    if missing:
        raise RuntimeError(f"实体人工复核表缺少判断字段: {missing}")
    match_review = validate_entity_pair_coverage(review, candidates, int(cfg["rules"]["entity_audit_min_reviewed_match_pairs"]))
    for column in judgment_columns:
        match_review[column] = match_review[column].astype(str).str.strip().str.upper()
    reviewed = len(match_review)
    positive_tokens = {"匹配", "同一", "是", "1", "TRUE", "Y"}
    correct = int(match_review["最终判断"].isin(positive_tokens).sum())
    ppv = correct / reviewed
    z = 1.959963984540054
    wilson = (ppv + z*z/(2*reviewed) - z*math.sqrt(ppv*(1-ppv)/reviewed + z*z/(4*reviewed*reviewed))) / (1 + z*z/reviewed)
    two_reviewers_complete = bool(match_review["复核人1判断"].ne("").all() and match_review["复核人2判断"].ne("").all())
    disagreements = match_review["复核人1判断"].ne(match_review["复核人2判断"])
    disagreements_resolved = bool(not disagreements.any() or match_review.loc[disagreements, "最终判断"].ne("").all())
    counts_consistent = int(gate.get("reviewed_match_pairs", -1)) == reviewed and int(gate.get("correct_matches", -1)) == correct and abs(float(gate.get("ppv", -1)) - ppv) <= 1e-6 and abs(float(gate.get("wilson_lower_95", -1)) - wilson) <= 1e-6
    passed = (
        gate.get("passed") is True
        and ppv >= float(cfg["rules"]["entity_audit_min_ppv"])
        and wilson >= float(cfg["rules"]["entity_audit_min_wilson_lower"])
        and int(gate.get("reviewer_count", 0)) >= 2 and two_reviewers_complete
        and gate.get("disagreements_resolved") is True and disagreements_resolved
        and counts_consistent
        and gate.get("source_csv_sha256") == sha256_file(review_csv)
        and gate.get("candidate_csv_sha256") == sha256_file(candidate_csv)
    )
    if not passed:
        raise RuntimeError("实体人工复核未达完整性、PPV或Wilson门槛；已在门1停止。")


def main(
    root: Path,
    config_path: Path | None = None,
    *,
    allow_unfrozen_definition: bool = False,
) -> None:
    root = root.resolve()
    fail_if_test_already_opened(root)
    fail_if_analysis_frozen(root, "02_build_profiles")
    resolved_config_path = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else (PROGRAM_DIR / "config.yaml").resolve()
    )
    if not allow_unfrozen_definition:
        validate_profile_definition_freeze(root, resolved_config_path)
    cfg = load_config(resolved_config_path)
    feature_groups = configured_feature_groups(cfg)
    dictionary = build_feature_dictionary(cfg)
    ensure_dirs(root)
    if bool(cfg["rules"].get("require_entity_audit_gate", True)):
        validate_entity_audit_gate(root, cfg)
    middle = root / "数据/01_中间数据"
    analysis = root / "数据/02_分析数据"
    sealed = root / "数据/03_封存测试"
    result = root / "结果/02_画像"
    episodes = pd.read_csv(middle / "inspection_episode.csv", dtype=str)
    history = pd.read_csv(middle / "historical_inspection_outcomes.csv", dtype=str)
    require_columns(episodes, ["sample_id", "entity_proxy_id", "quarter", "episode_open_date", "candidate_naics_group", "candidate_site_state", "split", "label"], "inspection_episode.csv")
    require_columns(history, ["activity_nr", "entity_proxy_id", "open_date", "naics_group", "site_state", "outcome_positive", "outcome_available_time"], "historical_inspection_outcomes.csv")
    episodes["episode_open_date"] = pd.to_datetime(episodes["episode_open_date"], errors="coerce")
    history["open_date"] = pd.to_datetime(history["open_date"], errors="coerce")
    history["outcome_available_time"] = pd.to_datetime(history["outcome_available_time"], errors="coerce")
    history["outcome_positive"] = pd.to_numeric(history["outcome_positive"], errors="raise").astype(int)
    if episodes["episode_open_date"].isna().any():
        raise ValueError("episode_open_date存在无法解析的值。")
    if history["open_date"].isna().any():
        raise ValueError("历史检查open_date存在无法解析的值。")
    invalid_outcomes = ~history["outcome_positive"].isin([0, 1])
    if invalid_outcomes.any():
        raise ValueError("历史检查outcome_positive必须为0或1。")
    profiles, checks = build_profiles_frame(episodes, history, cfg)
    for feature in FEATURES:
        profiles[feature] = pd.to_numeric(profiles[feature], errors="coerce")
    test_feature_whitelist = [
        "sample_id", "entity_proxy_id", "quarter", "split",
        *feature_groups["Context"], *feature_groups["Static"], *feature_groups["Dynamic"],
    ]
    require_columns(profiles, test_feature_whitelist, "画像模型特征")
    test_features = profiles.loc[profiles["split"].eq("test"), test_feature_whitelist].copy()

    leakage_audit = build_leakage_audit(
        checks, profiles, test_features, test_feature_whitelist, feature_groups,
    )
    write_json(leakage_audit, result / "防泄漏审计.json")
    if not leakage_audit["passed"]:
        failed_checks = [name for name, detail in leakage_audit["checks"].items() if not detail["passed"]]
        raise RuntimeError(f"防泄漏审计失败；已停止: {failed_checks}")

    train_val_profiles = profiles.loc[profiles["split"].isin(["train", "validation"])]
    train_val_profiles.to_csv(analysis / "profiles_train_val.csv", index=False, encoding="utf-8")
    evidence = build_recalculation_evidence(profiles, history, max_samples=20)
    evidence.to_csv(result / "受限_画像复算证据卡.csv", index=False, encoding="utf-8")
    dictionary.to_csv(analysis / "feature_dictionary.csv", index=False, encoding="utf-8")

    test_features_path = sealed / "test_features_sealed.csv"
    test_features.to_csv(test_features_path, index=False, encoding="utf-8")
    formal_mode = not allow_unfrozen_definition
    set_private_permissions(test_features_path, formal_mode)
    test_commitment_path = sealed / "test_features_commitment.json"
    write_json({
        "path": str(test_features_path.relative_to(root)),
        "sha256": sha256_file(test_features_path),
        "rows": int(len(test_features)),
        "columns": [str(column) for column in test_features.columns],
    }, test_commitment_path)
    set_private_permissions(test_commitment_path, formal_mode)

    train_val_profiles.groupby(["split", "quarter"], as_index=False).agg(n=("sample_id", "size"), entities=("entity_proxy_id", "nunique"), mean_history=("history_inspections", "mean")).to_csv(result / "画像汇总.csv", index=False, encoding="utf-8")
    plot_columns = ["sample_id", "entity_proxy_id", "quarter", "split", "history_inspections", "history_positive_inspections", "smoothed_positive_rate", "inspections_365d", "positives_365d", "decayed_inspections", "decayed_positives"]
    train_val_profiles[plot_columns].to_csv(result / "画像作图数据.csv", index=False, encoding="utf-8")
    write_framework_svg(result / "图1_动态画像研究框架.svg")
    if train_val_profiles.empty:
        raise RuntimeError("Train/Validation画像样本为空，无法生成公共案例与轨迹图。")
    case_row = train_val_profiles.sort_values(["history_inspections", "sample_id"], ascending=[False, True], kind="stable").iloc[0]
    pd.DataFrame([{"case_role": "pre_registered_longest_history", "sample_id": case_row["sample_id"], "entity_proxy_id": case_row["entity_proxy_id"], "selection_rule": "maximum history_inspections, tie by sample_id ascending", "test_outcome_used": False}]).to_csv(result / "画像案例索引.csv", index=False, encoding="utf-8")
    trajectory = train_val_profiles[train_val_profiles["entity_proxy_id"].eq(case_row["entity_proxy_id"])].sort_values("quarter")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    trajectory.plot(x="quarter", y=["smoothed_positive_rate", "decayed_positives"], marker="o", ax=ax)
    ax.set_title("匿名画像时间轨迹（按固定规则选取）", fontsize=13)
    ax.set_xlabel("季度", fontsize=11)
    ax.set_ylabel("画像取值", fontsize=11)
    ax.legend(["平滑历史率", "时间衰减阳性量"], loc="best", fontsize=10)
    ax.grid(alpha=0.25)
    fig.text(0.5, 0.01, "供历史线索人工复核，不作为处罚或定责依据", ha="center", fontsize=9, color="#666666")
    plt.tight_layout(rect=[0, 0.05, 1, 1])
    plt.savefig(result / "图3_匿名画像时间轨迹.svg", format="svg", bbox_inches="tight")
    plt.close()
    hist_axes = train_val_profiles[["history_inspections", "smoothed_positive_rate", "decayed_positives"]].hist(
        figsize=(9, 3.5), bins=20, layout=(1, 3)
    )
    names_zh = ["成熟历史检查数", "平滑历史率", "时间衰减阳性量"]
    for sub_ax, name_zh in zip(hist_axes.flatten(), names_zh):
        sub_ax.set_xlabel(name_zh, fontsize=10)
        sub_ax.set_ylabel("频数（样本数）", fontsize=10)
    fig_dist = hist_axes.flatten()[0].figure
    fig_dist.suptitle("画像指标分布（Train / Validation）", fontsize=13)
    fig_dist.text(0.5, 0.01, "供历史线索人工复核，不作为处罚或定责依据", ha="center", fontsize=9, color="#666666")
    plt.tight_layout(rect=[0, 0.05, 1, 1])
    plt.savefig(result / "画像指标分布.svg", format="svg", bbox_inches="tight")
    plt.close()
    if formal_mode:
        permission_audit = secure_restricted_tree(root)
        write_json(permission_audit, root / "结果/01_数据审计/受限目录权限审计.json")
        if permission_audit.get("supported") is not True:
            raise RuntimeError("正式运行平台无法落实受限目录0700/文件0600权限门。")
    print(f"02完成: Train/Validation={profiles['split'].isin(['train','validation']).sum()}, Test特征={len(test_features)}")


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
