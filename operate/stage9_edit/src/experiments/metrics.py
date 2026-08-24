"""阶段9指标计算骨架。

注意：正式指标需要学生3交付 benchmark_gold_restricted.jsonl 后才能计算。
当前函数已搭好接口，缺少 gold 时返回明确 TODO 状态，不伪造数字。
"""

from __future__ import annotations

from typing import Any


def _require_gold(gold: Any) -> str | None:
    if not gold:
        return "缺少 benchmark_gold_restricted.jsonl，无法计算正式指标"
    return None


def unsupported_claim_rate(outputs: list[dict[str, Any]], gold: Any = None) -> dict[str, Any]:
    block = _require_gold(gold)
    if block:
        return {"status": "TODO", "reason": block, "value": None}
    return {"status": "TODO", "reason": "待接入 gold 后实现", "value": None}


def citation_accuracy(outputs: list[dict[str, Any]], gold: Any = None) -> dict[str, Any]:
    block = _require_gold(gold)
    if block:
        return {"status": "TODO", "reason": block, "value": None}
    return {"status": "TODO", "reason": "待接入 gold 后实现", "value": None}


def safe_deferral_rate(outputs: list[dict[str, Any]], gold: Any = None) -> dict[str, Any]:
    block = _require_gold(gold)
    if block:
        return {"status": "TODO", "reason": block, "value": None}
    return {"status": "TODO", "reason": "待接入 gold 后实现", "value": None}


def first_attempt_pass_rate(outputs: list[dict[str, Any]], gold: Any = None) -> dict[str, Any]:
    return {"status": "TODO", "reason": "待锁定协议后实现", "value": None}


def cost_latency(outputs: list[dict[str, Any]], gold: Any = None) -> dict[str, Any]:
    return {"status": "TODO", "reason": "待接入模型调用计费信息后实现", "value": None}
