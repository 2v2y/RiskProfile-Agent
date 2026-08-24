"""阶段9消融实验骨架。

按研究方案 §31.9，需要分别删除：
- 内容审查（无独立审计）；
- 近期变化画像；
- 证据编号；
- 安全转人工。
"""

from __future__ import annotations

from typing import Any


ABLATION_CONFIGS: dict[str, dict[str, Any]] = {
    "no_audit": {
        "description": "删除内容审查模块",
        "method_variant": "B4",
        "enabled": False,
    },
    "no_recent_profile": {
        "description": "删除近期变化画像特征",
        "remove_fields": [
            "days_since_last_inspection",
            "days_since_last_positive",
            "inspections_365d",
            "inspections_730d",
            "positives_365d",
            "positives_730d",
            "decayed_inspections",
            "decayed_positives",
        ],
        "enabled": False,
    },
    "no_evidence_ids": {
        "description": "删除证据编号引用",
        "strip_reference_prefix": True,
        "enabled": False,
    },
    "no_safe_deferral": {
        "description": "删除安全转人工路径",
        "force_pass_when_deferred": True,
        "enabled": False,
    },
}


def run_ablations(runner: Any, profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """消融运行骨架：当前仅返回配置和 TODO 状态。"""
    return {
        "status": "TODO",
        "reason": "待阶段9主实验配置冻结后实现",
        "configs": ABLATION_CONFIGS,
    }
