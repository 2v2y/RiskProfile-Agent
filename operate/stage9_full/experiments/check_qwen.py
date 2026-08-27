"""服务器联通性自检：验证能否真实调用 Qwen，并解析出复核建议 JSON。

用途：正式实验前先跑这一条，确认 base_url / model ID / JSON 解析都没问题，
再跑 run_smoke / run_comparison，避免跑到一半才发现 Qwen 没连上。

运行（服务器，Qwen vLLM 已启动）：
    cd operate/stage9_full
    python -m experiments.check_qwen
"""

from __future__ import annotations

import json
import os
import re
import sys

from adapters import paths  # noqa: F401
from src.llm.client import DummyLLMClient, get_llm_client  # noqa: E402

EXPECTED_MODEL = "/DATA/models/Qwen3.8-27B"


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
    llm = config.get("llm", {})
    provider = llm.get("provider", "dummy")
    model = os.getenv("QWEN_MODEL") or llm.get("model", "")
    base_url = os.getenv("QWEN_BASE_URL") or llm.get("base_url", "")
    max_tokens = int(os.getenv("QWEN_MAX_TOKENS") or llm.get("max_tokens", 1024))

    print("=== Stage9 Qwen Check ===")
    print(f"provider  = {provider}")
    print(f"base_url  = {base_url}")
    print(f"model     = {model}")
    print(f"max_tokens= {max_tokens}")
    if provider == "qwen" and model != EXPECTED_MODEL:
        print(
            f"[WARN] model ID 应为 {EXPECTED_MODEL}（vLLM 实际暴露的 ID 含路径），"
            f"当前为 {model!r}"
        )

    try:
        client = get_llm_client(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 构造 LLM 客户端失败：{exc}")
        print("请检查 config/experiment_config.json 的 llm 段或 .env 的 QWEN_* 变量。")
        return 2

    if isinstance(client, DummyLLMClient):
        print("[SKIP] 当前 provider=dummy（离线），未做真实调用。")
        print("服务器正式运行把 config/experiment_config.json 的 llm.provider 设为 qwen 后再试。")
        return 3

    messages = [
        {"role": "system", "content": "只输出 JSON：{\"review_points\":[{\"point_id\":\"point_1\",\"focus_zh\":\"一句话\",\"basis_profile_facts\":[],\"regulation_refs\":[],\"missing_field_info\":[],\"verification_instructions_zh\":\"\"}]}"},
        {"role": "user", "content": "测试：请返回一个空建议。"},
    ]
    try:
        text = client.generate(messages)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 调用 Qwen 失败：{exc}")
        print("常见原因：vLLM 服务没起（curl http://127.0.0.1:8000/v1/models）、model ID 不匹配、langchain-openai 未装。")
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
