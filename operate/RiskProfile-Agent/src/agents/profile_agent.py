"""画像整理模块（Profile Agent）。

只读取白名单字段，把画像转成结构化原子事实；每个数字带 profile:<field> 溯源。
禁止重算、补造或作违法判断。阶段1为确定性实现，不调用 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import ProfileCard


# 每个字段允许的中文表述模板（方案 14.2）。字段缺省时用通用模板。
_STATEMENT_TEMPLATES: dict[str, str] = {
    "history_inspections": "截至该季度，历史共有{n}次成熟检查",
    "history_positive_inspections": "截至该季度，历史共有{n}次签发违章记录的成熟检查",
    "smoothed_positive_rate": "平滑历史比例（加1平滑）为{v}",
    "days_since_last_inspection": "最近一次可用检查距画像截止日{n}天",
    "days_since_last_positive": "最近一次签发违章记录的检查距画像截止日{n}天",
    "inspections_365d": "近365天共有{n}次成熟历史检查",
    "positives_365d": "近365天共有{n}次签发违章记录的成熟检查",
    "inspections_730d": "近730天共有{n}次成熟历史检查",
    "positives_730d": "近730天共有{n}次签发违章记录的成熟检查",
    "decayed_inspections": "时间衰减后的历史检查量为{v}",
    "decayed_positives": "时间衰减后的违章记录量为{v}",
    "historical_standard_codes": "历史记录涉及的OSHA标准编号包括：{v}",
    "historical_risk_categories": "历史风险类别包括：{v}",
    "risk_score": "冻结风险分数为{v}",
    "risk_percentile": "季度内风险分位为前{pct}%",
}


class ProfileAgent:
    """画像整理模块：画像卡 -> 结构化原子事实。"""

    def __init__(self, whitelist_path: Path | str | None = None):
        self.whitelist: set[str] | None = None
        if whitelist_path is not None:
            data = json.loads(Path(whitelist_path).read_text(encoding="utf-8"))
            self.whitelist = set(data["allowed_fields"])

    def _check_whitelist(self, profile: dict[str, Any]) -> list[str]:
        """返回不在白名单中的字段名（默认不启用白名单检查时为空）。"""
        if self.whitelist is None:
            return []
        return [k for k in profile if k not in self.whitelist]

    @staticmethod
    def _format(field: str, value: Any) -> str:
        if field in _STATEMENT_TEMPLATES:
            if field == "risk_percentile":
                pct = round(float(value) * 100)
                return _STATEMENT_TEMPLATES[field].format(pct=pct)
            if isinstance(value, (int, float)) and field in {
                "history_inspections",
                "history_positive_inspections",
                "days_since_last_inspection",
                "days_since_last_positive",
                "inspections_365d",
                "positives_365d",
                "inspections_730d",
                "positives_730d",
            }:
                n = int(value)
                return _STATEMENT_TEMPLATES[field].format(n=n)
            return _STATEMENT_TEMPLATES[field].format(v=value)
        return f"画像字段 {field} 的值为 {value}"

    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        violations = self._check_whitelist(profile)
        if violations:
            raise ValueError(f"画像包含白名单外字段（禁止传给智能模块）：{sorted(violations)}")

        # 运行时强校验（对应 schemas/profile_schema.json）
        card = ProfileCard.model_validate(profile)

        facts: list[dict[str, Any]] = []
        for field, value in card.model_dump().items():
            if value is None:
                continue
            facts.append(
                {
                    "fact_id": f"fact_{field}",
                    "statement_zh": self._format(field, value),
                    "field": field,
                    "value": value,
                    "provenance": f"profile:{field}",
                }
            )

        return {
            "sample_id": card.sample_id,
            "n_facts": len(facts),
            "facts": facts,
            "whitelist_violations": [],
        }
