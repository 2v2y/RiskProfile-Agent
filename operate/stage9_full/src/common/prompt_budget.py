"""Qwen 输入 token 预算：估算、裁剪与强制闸门（解决 vLLM max_model_len=8192 超长）。

背景：服务器 vLLM 当前 max_model_len=8192，input + output <= 8192。
Stage9 约定（config/experiment_config.json -> llm.prompt_budget）：
    max_input_tokens   = 6000   （输入预算，明显低于 8192）
    max_tokens         = 1024   （输出预算）
    safety_tokens      = 600    （8192 - 6000 - 1024 = 1168 安全余量中的一部分）

估算采用**保守**启发式（服务器无真实 tokenizer 依赖）：
    CJK 字符 ≈ 1 token/字；其余字符 ≈ 3 字符/token；每条消息加固定开销。
即使真实 tokenizer 计数比启发式偏高，也由两道保险兜底：
    1) 构造端裁剪（evidence text / facts / profile 长字段）；
    2) 客户端硬闸门：字符数硬上限 + 估算硬上限，超限继续裁剪直到满足。

裁剪只影响“发送给 LLM 的文本”，不影响检索结果、证据清单与评价输入
（outputs 中 retrieval.items 仍保留完整原文；引用键 evidence_id/document_id 不丢失）。
"""

from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_MAX_INPUT_TOKENS = 6000
DEFAULT_MAX_CONTEXT_TOKENS = 8192
DEFAULT_MAX_EVIDENCE_CHARS = 300
DEFAULT_MAX_FACTS = 30
DEFAULT_SAFETY_TOKENS = 600

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: Any) -> int:
    """保守估算单段文本 token：CJK≈1 token/字，其他≈3 字符/token。"""
    s = str(text)
    if not s:
        return 0
    cjk = len(_CJK_RE.findall(s))
    other = len(s) - cjk
    return cjk + int(other / 3) + 1


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估算整个消息序列 token（含 role 与每条消息的 chat 模板开销）。"""
    overhead = 12 * len(messages)
    return (
        sum(
            estimate_tokens(m.get("content")) + estimate_tokens(m.get("role"))
            for m in messages
        )
        + overhead
    )


def compact_text(text: Any, max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS) -> str:
    s = str(text)
    return s if len(s) <= max_chars else s[:max_chars] + "…[已截断]"


def compact_profile(profile: dict[str, Any], max_chars: int = 100) -> dict[str, Any]:
    """画像卡压缩：超长字符串字段（如 score_evidence）截断，保留全部键。"""
    out: dict[str, Any] = {}
    for k, v in profile.items():
        out[k] = compact_text(v, max_chars) if isinstance(v, str) else v
    return out


def _compact_value(value: Any, max_chars: int = 120, max_items: int = 8) -> Any:
    """把事实 value 压缩：标量原样；长字符串截断；list/dict 只留前 max_items 项。"""
    if isinstance(value, str):
        return compact_text(value, max_chars)
    if isinstance(value, list):
        return [_compact_value(v, max_chars, max_items) for v in value[:max_items]]
    if isinstance(value, dict):
        return {
            k: _compact_value(v, max_chars, max_items)
            for k, v in list(value.items())[:max_items]
        }
    return value


def compact_facts(
    facts: list[dict[str, Any]] | None,
    max_facts: int = DEFAULT_MAX_FACTS,
    max_chars: int = 150,
) -> list[dict[str, Any]]:
    """画像事实压缩（LLM 视图）：

    - 去掉 fact_id / provenance 等重复元数据（statement_zh 已由 value 生成，属于重复信息）；
    - 只保留 field + statement_zh + 压缩后的 value；
    - statement 截断到 max_chars；value 长字符串截断、list/dict 只留前 8 项；
    - 超过条数上限时按原顺序保留前 max_facts 条。
    """
    out: list[dict[str, Any]] = []
    for f in (facts or [])[:max_facts]:
        item = dict(f)
        out.append(
            {
                "field": str(item.get("field", "")),
                "statement_zh": compact_text(item.get("statement_zh", ""), max_chars),
                "value": _compact_value(item.get("value")),
            }
        )
    return out


def compact_evidence(
    items: list[Any] | None,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    max_items: int = 3,
) -> list[dict[str, Any]]:
    """证据压缩：保留全部条目（引用键不丢失），只截断 text/title；顺序 = 检索相关性顺序。"""
    out: list[dict[str, Any]] = []
    for it in (items or [])[:max_items]:
        item = it.model_dump() if hasattr(it, "model_dump") else dict(it)
        for k in ("text", "title"):
            if isinstance(item.get(k), str):
                item[k] = compact_text(item[k], max_chars)
        out.append(item)
    return out


def _user_char_total(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages if m.get("role") == "user")


def _trim_evidence_text(messages: list[dict[str, Any]], max_chars: int) -> bool:
    """把 user 载荷中 evidence/法规证据 的 text 字段统一截断到 max_chars。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("evidence", "法规证据"):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("text"), str):
                    cut = compact_text(it["text"], max_chars)
                    if cut != it["text"]:
                        it["text"] = cut
                        changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def _trim_fact_fields(messages: list[dict[str, Any]], max_chars: int) -> bool:
    """把 user 载荷中 facts/画像事实/claims 的文本字段截断到 max_chars。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("profile_facts", "画像事实"):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                for f in ("statement_zh", "value", "text"):
                    if isinstance(it.get(f), str) and len(it[f]) > max_chars:
                        it[f] = compact_text(it[f], max_chars)
                        changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def _cap_fact_count(messages: list[dict[str, Any]], max_facts: int) -> bool:
    """把 user 载荷中 facts/画像事实 的条数压到 max_facts（保留顺序前部）。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("profile_facts", "画像事实"):
            items = data.get(key)
            if isinstance(items, list) and len(items) > max_facts:
                data[key] = items[:max_facts]
                changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def _cap_evidence_count(messages: list[dict[str, Any]], max_items: int) -> bool:
    """把 user 载荷中 evidence/法规证据 的条数压到 max_items（保留相关性顺序前部）。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("evidence", "法规证据"):
            items = data.get(key)
            if isinstance(items, list) and len(items) > max_items:
                data[key] = items[:max_items]
                changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def _trim_profile_fields(messages: list[dict[str, Any]], max_chars: int = 100) -> bool:
    """把 user 载荷中 profile 的长字符串字段截断（score_evidence 等）。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("profile"), dict):
            continue
        for k, v in data["profile"].items():
            if isinstance(v, str) and len(v) > max_chars:
                data["profile"][k] = compact_text(v, max_chars)
                changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def _json_safe_shrink(messages: list[dict[str, Any]], hard_chars: int) -> bool:
    """JSON 安全收缩（硬截断前最后手段）：保留 facts 前 10 条与 evidence 前 1 条，丢弃 profile。"""
    changed = False
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(str(msg["content"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data.pop("profile", None)
        for key in ("profile_facts", "画像事实"):
            if isinstance(data.get(key), list) and len(data[key]) > 10:
                data[key] = data[key][:10]
                changed = True
        for key in ("evidence", "法规证据"):
            if isinstance(data.get(key), list) and len(data[key]) > 1:
                data[key] = data[key][:1]
                changed = True
        if changed:
            msg["content"] = json.dumps(data, ensure_ascii=False)
    return changed


def enforce_input_budget(
    messages: list[dict[str, Any]],
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    hard_chars: int | None = None,
    evidence_caps: tuple[int, ...] = (300, 200, 120),
    fact_caps: tuple[int, ...] = (30, 24, 18, 12),
    evidence_counts: tuple[int, ...] = (3, 2, 1),
) -> dict[str, Any]:
    """把 messages 裁剪到输入预算内，返回裁剪报告。

    顺序（由轻到重，全部在 JSON 结构内进行，不破坏引用键）：
      1. evidence text / fact 文本按档位截断；
      2. facts 条数按档位收缩；
      3. evidence 条数按档位收缩（保留相关性顺序前部）；
      4. 仍超限时对 user content 做整段字符硬截断（最后手段，含截断标记）。
    """
    report: dict[str, Any] = {
        "max_input_tokens": max_input_tokens,
        "before_tokens": messages_tokens(messages),
        "before_chars": _user_char_total(messages),
        "trimmed": [],
    }
    hard_chars = hard_chars or int(max_input_tokens)
    rounds = max(len(evidence_caps), len(fact_caps), len(evidence_counts))
    for i in range(rounds):
        ev_cap = evidence_caps[i] if i < len(evidence_caps) else evidence_caps[-1]
        fa_cap = fact_caps[i] if i < len(fact_caps) else fact_caps[-1]
        ev_cnt = evidence_counts[i] if i < len(evidence_counts) else evidence_counts[-1]
        if _trim_evidence_text(messages, ev_cap):
            report["trimmed"].append(f"evidence_text<={ev_cap}")
        fact_text_cap = min(150, ev_cap + 50)
        if _trim_fact_fields(messages, fact_text_cap):
            report["trimmed"].append(f"fact_text<={fact_text_cap}")
        if _cap_fact_count(messages, fa_cap):
            report["trimmed"].append(f"facts<={fa_cap}")
        if _cap_evidence_count(messages, ev_cnt):
            report["trimmed"].append(f"evidence_items<={ev_cnt}")
        if _trim_profile_fields(messages, 100):
            report["trimmed"].append("profile_text<=100")
        if messages_tokens(messages) <= max_input_tokens and _user_char_total(messages) <= hard_chars:
            break
    if messages_tokens(messages) > max_input_tokens or _user_char_total(messages) > hard_chars:
        if _json_safe_shrink(messages, hard_chars):
            report["trimmed"].append("json_safe_shrink(profile dropped, facts<=10, evidence<=1)")
    if messages_tokens(messages) > max_input_tokens or _user_char_total(messages) > hard_chars:
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = str(msg["content"])
            if len(content) > hard_chars:
                msg["content"] = content[:hard_chars] + "\n…[已按 context budget 硬截断]"
                report["trimmed"].append(f"user_content_hard_cap<={hard_chars}")
                break
    report["after_tokens"] = messages_tokens(messages)
    report["after_chars"] = _user_char_total(messages)
    return report


def prepare_messages(
    messages: list[dict[str, Any]],
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    output_tokens: int = 1024,
    safety_tokens: int = DEFAULT_SAFETY_TOKENS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """客户端统一闸门：计算可用输入预算并强制裁剪，返回 (messages, 报告)。

    可用输入预算 = min(配置输入预算, max_context - output - safety)。
    """
    available = min(
        max_input_tokens,
        max(1, max_context_tokens - output_tokens - safety_tokens),
    )
    report = enforce_input_budget(
        messages, max_input_tokens=available, hard_chars=available
    )
    report.update(
        {
            "max_context_tokens": max_context_tokens,
            "output_tokens": output_tokens,
            "safety_tokens": safety_tokens,
            "available_input_tokens": available,
            "estimated_total": report["after_tokens"] + output_tokens,
        }
    )
    return messages, report


def budget_summary(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "estimated_input_tokens": messages_tokens(messages),
        "n_messages": len(messages),
        "chars": sum(len(str(m.get("content", ""))) for m in messages),
    }
