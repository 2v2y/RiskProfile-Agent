"""Stage9 离线自检（OFFLINE / STRUCTURAL TEST，完全不调用 Qwen）。

一次检查：
  1. 项目根目录（stage9_full 自包含）
  2. Python import（adapters / src / baselines / evaluation）
  3. experiment_config 结构与路径
  4. 全部 data 文件（profiles / supplement / knowledge / benchmark / red team /
     gold / manifest / lookup / whitelist）
  5. RAG 数据（chunks / mapping / inventory / vector_db）
  6. FAISS index 是否可加载（缺 faiss 依赖时 [SKIP]）
  7. BGE 本地模型路径（服务器路径不存在时 [WARN]，不视为失败）
  8. Qwen 配置（provider / base_url / model ID / max_tokens）
  9. 是否存在其他 stage / 外部交付数据目录的运行时依赖
 10. Prompt 输入预算（构造真实 review prompt 估算 token，<=6000+余量）
 11. 离线数据流（dummy LLM + 关键词回退跑 B0—B5 一个样本）

运行：
    cd operate/stage9_full
    python -m experiments.check_stage9

失败时输出 [MISSING] / [IMPORT ERROR] / [PATH ERROR] / [CONFIG ERROR]，
不会抛出原始 traceback。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from adapters import paths  # noqa: F401

# 运行时禁止引用的外部路径/模块。字符串刻意用拼接构造，
# 避免自检脚本自身命中“禁止字符串”搜索。
FORBIDDEN_RUNTIME = tuple(
    a + b
    for a, b in (
        ("origin_", "data"),
        ("from_", "student1"),
        ("from_", "student2"),
        ("from_", "student3"),
        ("operate/stage", "1"),
        ("operate/stage9_", "edit"),
        ("import stage", "1"),
        ("from stage", "1"),
    )
)

FAIL_STATUSES = {"MISSING", "IMPORT ERROR", "PATH ERROR", "CONFIG ERROR"}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in _read_text(path).splitlines()
        if line.strip()
    ]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    rows = list(csv.DictReader(io.StringIO(_read_text(path))))
    header = list(rows[0].keys()) if rows else []
    return header, rows


class _CaptureClient:
    """记录发送给 LLM 的 messages，返回合法复核建议 JSON（不联网）。"""

    model = "capture"

    def __init__(self):
        self.messages: list[dict[str, str]] | None = None

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.messages = list(messages)
        return json.dumps(
            {
                "review_points": [
                    {
                        "point_id": "point_1",
                        "focus_zh": "自检占位：请用真实 Qwen 生成复核建议",
                        "basis_profile_facts": [],
                        "regulation_refs": [],
                        "missing_field_info": ["自检未调用 Qwen"],
                        "verification_instructions_zh": "由人工核实现场情况",
                    }
                ]
            },
            ensure_ascii=False,
        )


def main(argv: list[str] | None = None) -> int:
    results: list[tuple[str, str, str]] = []

    def record(name: str, status: str, detail: str = "") -> None:
        results.append((name, status, detail))
        suffix = f" - {detail}" if detail else ""
        print(f"[{status}] {name}{suffix}")

    print("=== Stage9 Self Check ===")

    # 1) project root
    root = paths.stage9_root()
    record("project root", "OK", str(root))

    # 2) config
    config_path = root / "config" / "experiment_config.json"
    config: dict[str, Any] = {}
    if not config_path.exists():
        record("config", "PATH ERROR", f"缺少 {config_path}")
    else:
        try:
            config = json.loads(_read_text(config_path))
            record("config", "OK", f"{config_path.name}（{config.get('version', '?')}）")
        except Exception as exc:  # noqa: BLE001
            record("config", "CONFIG ERROR", f"JSON 解析失败：{exc}")

    required_config_keys = {
        "data", "retrieval", "review", "llm", "audit", "semantic_audit",
        "orchestrator", "prompts", "registries", "baselines", "evaluation",
    }
    if config:
        missing_keys = sorted(required_config_keys - set(config))
        if missing_keys:
            record("config keys", "CONFIG ERROR", f"缺少段：{missing_keys}")
        else:
            record("config keys", "OK")
        bad_data_refs = [
            v
            for v in config.get("data", {}).values()
            if any(p in str(v).lower() for p in FORBIDDEN_RUNTIME)
        ]
        if bad_data_refs:
            record("config data paths", "CONFIG ERROR", f"仍引用外部路径：{bad_data_refs}")
        else:
            record("config data paths", "OK", "全部指向 data/ 内部")
        prompt_refs = config.get("prompts") or {}
        missing_prompts = [
            name
            for name, rel in prompt_refs.items()
            if not (root / rel).exists()
        ]
        if missing_prompts:
            record("prompt files", "MISSING", f"{missing_prompts}")
        else:
            record(
                "prompt files",
                "OK",
                f"{len(prompt_refs)} 个：{', '.join(sorted(prompt_refs))}",
            )

    # 3) imports
    import_targets = [
        "adapters.paths",
        "adapters.data_loader",
        "adapters.schema_adapter",
        "adapters.validator",
        "adapters.retrieval_adapter",
        "adapters.canonical_standard",
        "src.llm.client",
        "src.common.pydantic_schemas",
        "src.common.run_log",
        "src.common.prompt_budget",
        "src.agents.profile_agent",
        "src.agents.review_agent",
        "src.agents.audit_agent",
        "src.agents.semantic_audit_agent",
        "src.agents.retrieval_agent",
        "src.orchestrator.graph",
        "src.retrieval.rag_retriever",
        "baselines.base",
        "evaluation.metrics",
        "evaluation.bootstrap",
        "evaluation.error_analysis",
        "experiments.common",
    ]
    import_errors: list[str] = []
    for target in import_targets:
        try:
            __import__(target)
        except Exception as exc:  # noqa: BLE001
            import_errors.append(f"{target}: {exc}")
    if import_errors:
        record("python imports", "IMPORT ERROR", "；".join(import_errors[:5]))
    else:
        record("python imports", "OK", f"{len(import_targets)} 个模块")

    # 4) data files
    data = paths.resolve_data_paths(config) if config else {}
    expected_rows = {
        "profiles_train_val": 5337,
        "profile_supplement": 5337,
        "benchmark_cases": 5337,
        "red_team_cases": 240,
        "gold": 5337,
        "lookup": 5577,
    }
    if data:
        for key in (
            "profiles_train_val",
            "profile_supplement",
            "benchmark_cases",
            "red_team_cases",
            "gold",
            "manifest",
            "lookup",
            "whitelist",
        ):
            p = data.get(key)
            if p is None or not Path(p).exists():
                record(f"data:{key}", "MISSING", str(p))
                continue
            try:
                if str(p).endswith(".jsonl"):
                    rows = _read_jsonl(Path(p))
                    detail = f"{len(rows)} 行"
                    expect = expected_rows.get(key)
                    if expect and len(rows) != expect:
                        detail += f"（期望 {expect}，WARN）"
                        record(f"data:{key}", "WARN", detail)
                    else:
                        record(f"data:{key}", "OK", detail)
                elif str(p).endswith(".csv"):
                    header, rows = _read_csv(Path(p))
                    detail = f"{len(rows)} 行"
                    expect = expected_rows.get(key)
                    if expect and len(rows) != expect:
                        detail += f"（期望 {expect}，WARN）"
                        record(f"data:{key}", "WARN", detail)
                    else:
                        record(f"data:{key}", "OK", detail)
                else:
                    parsed = json.loads(_read_text(Path(p)))
                    if key == "whitelist":
                        whitelist_keys = {
                            "allow_read_fields",
                            "whitelisted_fields",
                            "allowed_fields",
                        }
                        has_fields = any(
                            isinstance(parsed.get(k), list) and parsed.get(k)
                            for k in whitelist_keys
                        )
                        if not has_fields:
                            record(
                                f"data:{key}",
                                "CONFIG ERROR",
                                f"白名单缺少字段列表（{sorted(whitelist_keys)}）",
                            )
                        else:
                            record(f"data:{key}", "OK", str(Path(p).name))
                    else:
                        record(f"data:{key}", "OK", str(Path(p).name))
            except Exception as exc:  # noqa: BLE001
                record(f"data:{key}", "CONFIG ERROR", f"读取失败：{exc}")

    # 5) RAG data
    knowledge_dir = Path(data["knowledge_dir"]) if data and data.get("knowledge_dir") else root / "data" / "knowledge"
    rag_files = {
        "chunks": knowledge_dir / "chunks" / "regulation_chunks.jsonl",
        "mapping": knowledge_dir / "standard_document_mapping.csv",
        "inventory": knowledge_dir / "document_inventory.csv",
        "kb_manifest": knowledge_dir / "knowledge_manifest.json",
        "faiss": knowledge_dir / "vector_db" / "faiss_index.bin",
        "chunk_ids": knowledge_dir / "vector_db" / "chunk_ids.json",
        "db_meta": knowledge_dir / "vector_db" / "db_meta.json",
        "embeddings": knowledge_dir / "vector_db" / "embeddings.npy",
    }
    for name, p in rag_files.items():
        if not p.exists():
            record(f"rag:{name}", "MISSING", str(p))
        else:
            record(f"rag:{name}", "OK", f"{p.stat().st_size / 1024:.0f} KB")
    n_chunks = 0
    if rag_files["chunks"].exists():
        try:
            n_chunks = len(_read_jsonl(rag_files["chunks"]))
            if n_chunks != 22781:
                record("rag:chunks count", "WARN", f"{n_chunks}（期望 22781）")
            else:
                record("rag:chunks count", "OK", "22781")
        except Exception as exc:  # noqa: BLE001
            record("rag:chunks count", "CONFIG ERROR", str(exc))
    if rag_files["db_meta"].exists():
        try:
            meta = json.loads(_read_text(rag_files["db_meta"]))
            record(
                "rag:db_meta",
                "OK",
                f"{meta.get('db_version')} / model_name={meta.get('model_name')}（仅版本记录，加载用本地路径）",
            )
        except Exception as exc:  # noqa: BLE001
            record("rag:db_meta", "CONFIG ERROR", str(exc))

    # 6) RAG retriever
    retriever_py = root / "src" / "retrieval" / "rag_retriever.py"
    if not retriever_py.exists():
        record("RAG retriever", "MISSING", str(retriever_py))
    else:
        record("RAG retriever", "OK", str(retriever_py.relative_to(root)))

    # 7) FAISS index load（缺依赖时 SKIP）
    try:
        import faiss  # noqa: F401

        faiss_available = True
    except Exception:  # noqa: BLE001
        faiss_available = False
    if not faiss_available:
        record("FAISS", "SKIP", "faiss 未安装（服务器安装 faiss-cpu 后自动检查）")
    elif rag_files["faiss"].exists():
        try:
            import faiss

            index = faiss.read_index(str(rag_files["faiss"]))
            record("FAISS", "OK", f"ntotal={index.ntotal}, dim={index.d}")
        except Exception as exc:  # noqa: BLE001
            record("FAISS", "MISSING", f"加载失败：{exc}")

    # 8) BGE local model（服务器路径；本机未挂载时 WARN，不视为失败）
    bge_path = os.getenv("BGE_MODEL_PATH") or (
        (config.get("retrieval") or {}).get("bge_model_path")
        or "/DATA/models/bge-small-en-v1.5"
    )
    if not Path(bge_path).is_dir():
        record("BGE local model", "WARN", f"{bge_path} 不存在（服务器模型路径，本机不挂载属正常）")
    else:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:  # noqa: BLE001
            record("BGE local model", "SKIP", f"{bge_path} 存在但 sentence-transformers 未安装")
        else:
            try:
                from src.retrieval.rag_retriever import _load_sentence_transformer

                _load_sentence_transformer(bge_path)
                record("BGE local model", "OK", f"{bge_path}")
            except Exception as exc:  # noqa: BLE001
                record("BGE local model", "MISSING", f"加载失败：{exc}")

    # 9) LLM client / Qwen config
    llm = config.get("llm", {})
    record("LLM client", "OK", f"provider={llm.get('provider')}, model={llm.get('model')}, max_tokens={llm.get('max_tokens')}")
    if llm.get("provider") == "qwen":
        if llm.get("model") != "/DATA/models/Qwen3.8-27B":
            record("Qwen model ID", "CONFIG ERROR", f"应为 /DATA/models/Qwen3.8-27B，实际 {llm.get('model')!r}")
        else:
            record("Qwen model ID", "OK", "/DATA/models/Qwen3.8-27B")
        if llm.get("base_url") != "http://127.0.0.1:8000/v1":
            record("Qwen base_url", "WARN", f"{llm.get('base_url')}（期望 http://127.0.0.1:8000/v1）")
        else:
            record("Qwen base_url", "OK", "http://127.0.0.1:8000/v1")
        mt = int(llm.get("max_tokens") or 0)
        if mt > 1024:
            record("max_tokens", "CONFIG ERROR", f"{mt}（应 <=1024，配合 input<=6000）")
        elif mt <= 0:
            record("max_tokens", "CONFIG ERROR", "缺失或非法")
        else:
            record("max_tokens", "OK", f"{mt}")
    try:
        from src.llm.client import get_llm_client

        get_llm_client(config)
        record("LLM client construct", "OK", "可构造（不联网）")
    except Exception as exc:  # noqa: BLE001
        record("LLM client construct", "CONFIG ERROR", str(exc))

    # 10) no external project dependency
    forbidden_hits: list[str] = []
    py_files = [
        p
        for p in root.rglob("*.py")
        if "results" not in p.parts and "docs" not in p.parts and "__pycache__" not in p.parts
    ]
    for p in py_files:
        text = _read_text(p)
        for line_no, line in enumerate(text.splitlines(), start=1):
            low = line.lower()
            if any(f in low for f in FORBIDDEN_RUNTIME):
                forbidden_hits.append(f"{p.relative_to(root)}:{line_no}")
    if config_path.exists():
        low_cfg = _read_text(config_path).lower()
        if any(f in low_cfg for f in FORBIDDEN_RUNTIME):
            forbidden_hits.append("config/experiment_config.json")
    if forbidden_hits:
        record("no external project dependency", "CONFIG ERROR", "；".join(forbidden_hits[:10]))
    else:
        record("no external project dependency", "OK", "代码与 config 无外部交付目录引用")

    # 数据清单（manifests）允许出现来源备注（provenance），只提示不失败。
    data_json_hits = 0
    for p in (root / "data").rglob("*.json"):
        try:
            text = _read_text(p)
        except Exception:  # noqa: BLE001
            continue
        low = text.lower()
        if any(f in low for f in FORBIDDEN_RUNTIME):
            data_json_hits += 1
    if data_json_hits:
        record(
            "data manifest provenance",
            "WARN",
            f"{data_json_hits} 个数据清单含来源备注（非运行时路径，可忽略）",
        )
    else:
        record("data manifest provenance", "OK")

    # 11) prompt budget（构造真实 review prompt，估算 token）
    budget = _check_prompt_budget(root, config)
    if budget is None:
        record("prompt budget", "WARN", "未能构造样例 prompt（样本无可用画像/证据）")
    else:
        tokens, detail = budget
        record(
            "prompt budget",
            "OK" if tokens <= 8192 else "CONFIG ERROR",
            detail,
        )

    # 12) offline data flow（dummy LLM + 关键词回退，B0—B5 一个样本）
    flow = _check_offline_flow(config)
    if flow is None:
        record("offline data flow", "WARN", "无可运行的验证样本")
    else:
        ok, detail = flow
        record("offline data flow", "OK" if ok else "CONFIG ERROR", detail)

    # summary
    failed = [r for r in results if r[1] in FAIL_STATUSES]
    skipped = [r for r in results if r[1] == "SKIP"]
    warned = [r for r in results if r[1] == "WARN"]
    print()
    if failed:
        print(f"=== Stage9 self-check FAILED（{len(failed)} 项） ===")
        for name, status, detail in failed:
            print(f"[{status}] {name} - {detail}")
        return 1
    extra = []
    if skipped:
        extra.append(f"{len(skipped)} SKIP")
    if warned:
        extra.append(f"{len(warned)} WARN")
    suffix = f"（{', '.join(extra)}）" if extra else ""
    print(f"=== Stage9 self-check PASSED{suffix} ===")
    return 0


def _check_prompt_budget(
    root: Path, config: dict[str, Any]
) -> tuple[int, str] | None:
    """用真实数据构造 review prompt（ReviewAgent 生产路径），返回估算 token 与摘要。"""
    from adapters.retrieval_adapter import Stage9RetrievalAdapter
    from experiments import common
    from src.agents.profile_agent import ProfileAgent
    from src.agents.review_agent import ReviewAgent
    from src.common import prompt_budget as pb

    cfg, data, stage_config = common.setup()
    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split="validation", limit=20)
    for item in eval_set:
        card = item["card"]
        codes = card.get("historical_standard_codes") or []
        if not codes:
            continue
        adapter = Stage9RetrievalAdapter(data, top_k=3, use_rag=False)
        retrieval = adapter.run(codes, card.get("historical_risk_categories") or [], query_id=card["sample_id"])
        if not retrieval.items:
            continue
        facts = ProfileAgent(
            whitelist_path=stage_config["paths"]["whitelist"], strict=True
        ).run(card)["facts"]
        capture = _CaptureClient()
        review = ReviewAgent(
            max_points=3,
            model="budget-check",
            llm_client=capture,
            use_llm=True,
            prompt_path=stage_config["prompts"]["review_agent"],
            prompt_version="review_agent_v1",
            fail_on_llm_error=False,
        )
        review.run(card, facts, retrieval)
        if capture.messages:
            messages, report = pb.prepare_messages(
                capture.messages,
                max_input_tokens=6000,
                max_context_tokens=8192,
                output_tokens=1024,
            )
            parts: dict[str, int] = {}
            for m in messages:
                parts.setdefault(str(m.get("role")), 0)
                parts[str(m.get("role"))] += pb.estimate_tokens(m.get("content"))
            detail = (
                f"input_est={report['after_tokens']}/{report['available_input_tokens']} "
                f"+ output=1024 = {report['estimated_total']} <= 8192；"
                f"chars={report['after_chars']}；分项(est)={parts}；"
                f"trimmed={','.join(report['trimmed']) or '-'}"
            )
            return report["estimated_total"], detail
    return None


def _check_offline_flow(config: dict[str, Any]) -> tuple[bool, str] | None:
    """离线数据流：dummy provider + 关键词回退，B0—B5 各跑一个验证样本。"""
    import copy

    from experiments import common

    cfg, data, stage_config = common.setup()
    off = copy.deepcopy(stage_config)
    off["llm"]["provider"] = "dummy"
    off["retrieval"]["use_rag"] = False
    off["review"]["use_llm"] = False
    off["semantic_audit"]["use_llm"] = False

    loaded = common.load_everything(data)
    eval_set = common.build_evaluation_set(loaded, split="validation", limit=1)
    if not eval_set:
        return None
    card = eval_set[0]["card"]
    verdicts: list[str] = []
    for method in off["baselines"]:
        out = common.run_method(method, off, data, card)
        verdicts.append(f"{method}={out.get('final_verdict')}")
    ok = all(v.split("=")[1] in ("PASS", "DEFER", "REJECT") for v in verdicts)
    return ok, f"sample={card.get('sample_id')}，" + " ".join(verdicts)


if __name__ == "__main__":
    sys.exit(main())
