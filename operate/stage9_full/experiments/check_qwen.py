"""服务器联通性自检：验证能否真实调用 Qwen，并解析出复核建议 JSON。

用途：正式实验前先跑这一条，确认 .env / model 名 / JSON 解析都没问题，
再跑 run_comparison，避免几百个样本跑到一半才发现 Qwen 没连上。
"""

from __future__ import annotations

import json
import os
import re
import sys

from adapters import paths  # noqa: F401
from src.llm.client import DummyLLMClient, get_llm_client  # noqa: E402


def _parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    s = text.find("{")
    e = text.rfind("}")
    if s >= 0 and e > s:
        text = text[s : e + 1]
    return json.loads(text)


def main() -> int:
    config = paths.load_experiment_config()
    provider = config.get("llm", {}).get("provider", "dummy")
    print(f"config llm.provider = {provider}")
    print(f"QWEN_BASE_URL = {os.getenv('QWEN_BASE_URL')}")
    print(f"QWEN_MODEL    = {os.getenv('QWEN_MODEL')}")

    try:
        client = get_llm_client(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 构造 LLM 客户端失败：{exc}")
        print("请检查 .env 是否已复制并填写 QWEN_BASE_URL / QWEN_MODEL。")
        return 2

    if isinstance(client, DummyLLMClient):
        print("[SKIP] 当前 provider=dummy，未做真实调用。")
        print("把 config/experiment_config.json 的 llm.provider 改为 qwen 并配 .env 后再试。")
        return 3

    messages = [
        {"role": "system", "content": "只输出 JSON：{\"review_points\":[{\"point_id\":\"point_1\",\"focus_zh\":\"一句话\",\"basis_profile_facts\":[],\"regulation_refs\":[],\"missing_field_info\":[],\"verification_instructions_zh\":\"\"}]}"},
        {"role": "user", "content": "测试：请返回一个空建议。"},
    ]
    try:
        text = client.generate(messages)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 调用 Qwen 失败：{exc}")
        print("常见原因：服务没起（curl 8000 端口）、model 名不匹配、langchain-openai 未装。")
        return 1

    print(f"[OK] 收到 Qwen 返回，长度 {len(text)}")
    print("--- 返回前 500 字符 ---")
    print(text[:500])
    try:
        data = _parse(text)
        keys = list(data.keys())
        print(f"[OK] JSON 解析成功，顶层键：{keys}")
        if "review_points" in data:
            print("[OK] 含 review_points，格式符合复核建议输出。")
            return 0
        print("[WARN] 解析成功但没有 review_points 键。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] JSON 解析失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
