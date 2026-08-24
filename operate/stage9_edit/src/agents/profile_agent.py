"""画像整理模块（Profile Agent）。

把已经过 schema 校验的画像卡转换成结构化原子事实；每个数字带 profile:<field> 溯源。
只转述已有数字，不重新计算、不补造、不作违法判断。

白名单过滤由数据适配层（src/profiles/adapter.py）在进入本模块前完成；
本模块在 strict=True 时对输入字段做二次白名单校验：
- 白名单禁止字段（label/split/future_*/gold_* 等）-> 明确报错；
- 白名单外未知字段 -> 明确报错（不猜测字段含义）；
- 只有白名单允许字段和画像元数据字段可以进入事实生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common.pydantic_schemas import ProfileCard


class ProfileInputError(ValueError):
    """画像输入不符合白名单/格式要求时抛出的明确错误。"""


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

_METADATA_FIELDS = {"sample_id", "quarter", "ranking_cutoff", "profile_version", "industry_group"}

# ProfileCard 中允许出现的全部字段（元数据 + schema 属性）
_SCHEMA_FIELDS = set(ProfileCard.model_fields.keys())


class ProfileAgent:
    """画像整理模块：画像卡 -> 结构化原子事实。"""

    def __init__(self, whitelist_path: str | None = None, strict: bool = False):
        self.strict = strict
        self.whitelist_path = whitelist_path
        self._allow: set[str] | None = None
        self._forbidden: set[str] = set()
        if whitelist_path is not None:
            self._load_whitelist(whitelist_path)

    def _load_whitelist(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._allow = {item["field"] for item in data.get("allow_read_fields", [])}
        self._forbidden = {item["field"] for item in data.get("forbidden_fields", [])}

    def _enforce_whitelist(self, profile: dict[str, Any]) -> None:
        """输入字段必须来自白名单或画像元数据/Schema 字段，否则明确报错，不猜测含义。"""
        forbidden_hits: list[str] = []
        unknown_hits: list[str] = []
        for key in profile:
            if key in _METADATA_FIELDS or key in _SCHEMA_FIELDS:
                continue
            if self._allow is not None and key in self._allow:
                continue
            if key in self._forbidden:
                forbidden_hits.append(key)
            else:
                unknown_hits.append(key)
        if forbidden_hits or unknown_hits:
            msg = "画像输入包含不允许的字段，无法确认其含义，拒绝进入事实生成"
            if forbidden_hits:
                msg += f"；白名单禁止字段：{sorted(forbidden_hits)}"
            if unknown_hits:
                msg += f"；白名单外未知字段：{sorted(unknown_hits)}"
            raise ProfileInputError(msg)

    @staticmethod
    def _format(field: str, value: Any) -> str:
        if field in _STATEMENT_TEMPLATES:
            if field == "risk_percentile":
                return _STATEMENT_TEMPLATES[field].format(pct=round(float(value) * 100))
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
                return _STATEMENT_TEMPLATES[field].format(n=int(value))
            return _STATEMENT_TEMPLATES[field].format(v=value)
        if isinstance(value, bool):
            return f"画像字段 {field} 为{'是' if value else '否'}"
        return f"画像字段 {field} 的值为 {value}"

    def run(self, profile: dict[str, Any]) -> dict[str, Any]:
        if self.strict:
            self._enforce_whitelist(profile)
        card = ProfileCard.model_validate(profile)
        facts: list[dict[str, Any]] = []
        for field, value in card.model_dump().items():
            if field in _METADATA_FIELDS or value is None:
                continue
            # 空列表/空字典/空字符串不是事实，避免生成无意义陈述
            if value == "" or value == [] or value == {}:
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
        return {"sample_id": card.sample_id, "n_facts": len(facts), "facts": facts}
