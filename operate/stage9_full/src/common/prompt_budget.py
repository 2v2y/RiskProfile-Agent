"""Qwen 输入 token 预算：估算与压缩（解决 vLLM max_model_len=8192 超长问题）。

背景：服务器 vLLM 当前 max_model_len=8192，input + output <= 8192。
Stage9 约定（config/experiment_config.json -> llm.prompt_budget）：
    max_input_tokens  = 6000   （输入明显低于 8192，预留 1024 输出 + 余量）
    max_evidence_chars = 600   （每条法规证据正文只送前 600 字符给 LLM）
    max_facts          = 50    （画像原子事实上限）

token 估算是启发式（不做真实 tokenizer，避免额外依赖）：
    CJK 字符 ≈ 1 token；其余字符 ≈ 4 字符/token。
压缩只影响“发送给 LLM 的文本”，不影响检索结果 / 证据清单 / 评价输入
（outputs 中的 retrieval.items 仍保留完整原文）。
"""

from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_MAX_INPUT_TOKENS = 6000
DEFAULT_MAX_EVIDENCE_CHARS = 600
DEFAULT_MAX_FACTS = 50

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: Any) -> int:
    """启发式估算 token 数：CJK≈1 token/字，其他≈4 字符/token。"""
    s = str(text)
    if not s:
        return 0
    cjk = len(_CJK_RE.findall(s))
    other = len(s) - cjk
    return cjk + int(other / 4) + 1


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(
        estimate_tokens(m.get("content")) + estimate_tokens(m.get("role"))
        for m in messages
    )


def compact_text(text: Any, max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS) -> str:
    s = str(text)
    return s if len(s) <= max_chars else s[:max_chars] + "…[已截断]"


def compact_profile(profile: dict[str, Any], max_chars: int = 200) -> dict[str, Any]:
    """画像卡压缩：超长字符串字段（如 score_evidence）截断，保留全部键。"""
    out: dict[str, Any] = {}
    for k, v in profile.items():
        out[k] = compact_text(v, max_chars) if isinstance(v, str) else v
    return out


def compact_facts(
    facts: list[dict[str, Any]] | None,
    max_facts: int = DEFAULT_MAX_FACTS,
    max_chars: int = 400,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in (facts or [])[:max_facts]:
        item = dict(f)
        for k in ("statement_zh", "value"):
            if isinstance(item.get(k), str):
                item[k] = compact_text(item[k], max_chars)
        out.append(item)
    return out


def compact_evidence(
    items: list[Any] | None,
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    max_items: int = 3,
) -> list[dict[str, Any]]:
    """证据压缩：保留全部条目（evidence_id/document_id 等引用键不丢失），只截断 text/title。"""
    out: list[dict[str, Any]] = []
    for it in (items or [])[:max_items]:
        item = it.model_dump() if hasattr(it, "model_dump") else dict(it)
        for k in ("text", "title"):
            if isinstance(item.get(k), str):
                item[k] = compact_text(item[k], max_chars)
        out.append(item)
    return out


def enforce_input_budget(
    messages: list[dict[str, Any]],
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    evidence_min_chars: int = 200,
) -> list[dict[str, Any]]:
    """若 messages 估算超过预算，渐进截断 user 载荷中的 evidence/法规证据 text。

    返回截断后的 messages（原列表会被就地修改 user 消息内容）。
    """
    if messages_tokens(messages) <= max_input_tokens:
        return messages
    for _ in range(6):
        if messages_tokens(messages) <= max_input_tokens:
            break
        for msg in messages:
            if msg.get("role") != "user":
                continue
            try:
                data = json.loads(str(msg["content"]))
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            changed = False
            for key in ("evidence", "法规证据"):
                items = data.get(key)
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                        continue
                    cap = max(evidence_min_chars, int(len(it["text"]) * 0.6))
                    if len(it["text"]) > cap:
                        it["text"] = compact_text(it["text"], cap)
                        changed = True
            if changed:
                msg["content"] = json.dumps(data, ensure_ascii=False)
    return messages


def budget_summary(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """返回输入预算摘要（供 smoke / self-check 输出与审计）。"""
    return {
        "estimated_input_tokens": messages_tokens(messages),
        "n_messages": len(messages),
        "chars": sum(len(str(m.get("content", ""))) for m in messages),
    }
