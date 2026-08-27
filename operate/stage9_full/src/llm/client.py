"""Stage9 自包含 LLM 客户端（stage9_full/src/llm/client.py）。

用法：
    client = get_llm_client(config)     # 依据 config/experiment_config.json 的 llm.provider 选择
    text = client.generate([{"role":"user","content":"..."}])

- DummyLLMClient：离线占位，不发网络请求（离线自检 / 测试用）；
- QwenClient：通过 langchain-openai 的 ChatOpenAI 调用服务器 vLLM（OpenAI 兼容接口）。

服务器 Qwen 约定（不可联网下载，全部由 config/.env 控制）：
    provider   = qwen
    base_url   = http://127.0.0.1:8000/v1
    api_key    = EMPTY
    model      = /DATA/models/Qwen3.8-27B   （vLLM 实际模型 ID，含路径）
    max_tokens = 1024                       （vLLM max_model_len=8192，输入预算见
                                              src/common/prompt_budget.py，默认 input<=6000）

同时兼容环境变量覆盖：QWEN_BASE_URL / QWEN_API_KEY / QWEN_MODEL /
QWEN_TEMPERATURE / QWEN_MAX_TOKENS / QWEN_TIMEOUT。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Protocol

# 真实 LLM 调用日志：run_smoke / check_stage9 用它证明“确实调用了 Qwen”。
_LLM_CALL_LOG: list[dict[str, Any]] = []


def reset_llm_call_log() -> None:
    _LLM_CALL_LOG.clear()


def get_llm_call_log() -> list[dict[str, Any]]:
    return list(_LLM_CALL_LOG)


def _record_llm_call(record: dict[str, Any]) -> None:
    _LLM_CALL_LOG.append(record)


class LLMClient(Protocol):
    model: str

    def generate(self, messages: list[dict[str, str]]) -> str:
        """输入 messages（OpenAI chat 格式），返回文本。"""
        ...


class DummyLLMClient:
    """Stage9 离线占位实现：不发任何网络请求，供离线自检与测试。"""

    def __init__(self, model: str = "dummy-stage9"):
        self.model = model
        self.provider = "dummy"

    def generate(self, messages: list[dict[str, str]]) -> str:
        return (
            '{"review_points":[{"point_id":"point_1",'
            '"focus_zh":"离线占位：请用真实 Qwen 生成复核建议",'
            '"basis_profile_facts":[],"regulation_refs":[],'
            '"missing_field_info":["离线模式未调用 Qwen"],'
            '"verification_instructions_zh":"由人工核实现场情况"}]}'
        )


class QwenClient:
    """通过 LangChain 的 ChatOpenAI 调用服务器 vLLM 提供的 Qwen（OpenAI 兼容）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 120.0,
        max_context_tokens: int = 8192,
        input_budget_tokens: int = 6000,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider = "qwen"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_context_tokens = max_context_tokens
        self.input_budget_tokens = input_budget_tokens

    def generate(self, messages: list[dict[str, str]]) -> str:
        # 统一 context budget 闸门：所有真实 Qwen 调用（review / semantic audit /
        # check_qwen / 任何未来入口）都在这里强制裁剪，保证 input + max_tokens <= 8192。
        from src.common.prompt_budget import prepare_messages

        messages, report = prepare_messages(
            messages,
            max_input_tokens=self.input_budget_tokens,
            max_context_tokens=self.max_context_tokens,
            output_tokens=self.max_tokens,
        )
        print(
            "[budget] input_est={} (available={}) output={} chars={} total_est={}/{} trimmed={}".format(
                report["after_tokens"],
                report["available_input_tokens"],
                self.max_tokens,
                report["after_chars"],
                report["estimated_total"],
                self.max_context_tokens,
                ",".join(report["trimmed"]) or "-",
            ),
            file=sys.stderr,
        )
        record: dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "provider": "qwen",
            "model": self.model,
            "base_url": self.base_url,
            "input_est": report["after_tokens"],
            "output_tokens": self.max_tokens,
            "total_est": report["estimated_total"],
            "chars": report["after_chars"],
            "trimmed": report["trimmed"],
            "success": False,
            "error": None,
        }
        try:
            from langchain_openai import ChatOpenAI  # 懒加载：不装 langchain 时不影响其他模块

            llm = ChatOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
                extra_body={
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                    }
                },
            )
            response = llm.invoke(messages)
            content = response.content
            # Qwen/vLLM 返回的 content 可能是字符串，也可能是内容块列表。
            # 统一转成字符串，避免后续 .strip() / JSON 解析出错。
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        parts.append(str(block.get("text", "")))
                    else:
                        parts.append(str(block))
                text = "".join(parts)
            else:
                text = str(content)
            record.update({"success": True, "output_chars": len(text)})
            _record_llm_call(record)
            return text
        except Exception as exc:  # noqa: BLE001
            record.update({"success": False, "error": str(exc)[:300]})
            _record_llm_call(record)
            raise


def _env_or(name: str, default: Any) -> Any:
    value = os.getenv(name)
    return value if value is not None else default


def get_llm_client(config: dict[str, Any]) -> LLMClient:
    # .env 文件不会自动加载；这里在读取环境变量前显式加载，避免服务器上漏配。
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    llm_cfg = config.get("llm", {})
    # RP_LLM_PROVIDER 允许在不改 config 的情况下离线运行整条实验（dummy）。
    provider = os.getenv("RP_LLM_PROVIDER") or llm_cfg.get("provider", "dummy")
    if os.getenv("RP_LLM_PROVIDER") and os.getenv("RP_LLM_PROVIDER") != llm_cfg.get("provider"):
        print(
            f"[WARN] 环境变量 RP_LLM_PROVIDER={os.getenv('RP_LLM_PROVIDER')} "
            f"覆盖了 config 的 llm.provider={llm_cfg.get('provider')!r}",
            file=sys.stderr,
        )
    if provider == "qwen":
        base_url = str(_env_or("QWEN_BASE_URL", llm_cfg.get("base_url", "")))
        api_key = str(_env_or("QWEN_API_KEY", llm_cfg.get("api_key", "")))
        model = str(_env_or("QWEN_MODEL", llm_cfg.get("model", "")))
        if not base_url or not model:
            raise RuntimeError(
                "Qwen 配置不完整：QWEN_BASE_URL 和 QWEN_MODEL 必须配置。"
                "请在 .env 中填写并确保已加载，或 export 对应环境变量。"
                f"当前 base_url={base_url!r}，model={model!r}"
            )
        # vLLM 当前 max_model_len=8192：max_tokens 显式取 config（默认 1024），
        # 输入预算由 src/common/prompt_budget.py 在 prompt 构造端控制（input<=6000）。
        max_tokens = int(_env_or("QWEN_MAX_TOKENS", llm_cfg.get("max_tokens", 1024)))
        budget = llm_cfg.get("prompt_budget") or {}
        input_budget = int(
            _env_or("QWEN_INPUT_BUDGET", budget.get("max_input_tokens", 6000))
        )
        return QwenClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=float(_env_or("QWEN_TEMPERATURE", llm_cfg.get("temperature", 0.0))),
            max_tokens=max_tokens,
            timeout=float(_env_or("QWEN_TIMEOUT", llm_cfg.get("timeout", 120.0))),
            max_context_tokens=int(llm_cfg.get("max_context_tokens", 8192)),
            input_budget_tokens=input_budget,
        )
    return DummyLLMClient(model=llm_cfg.get("model") or "dummy-stage9")
