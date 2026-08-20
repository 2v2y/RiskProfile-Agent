"""最小端到端流水线：画像 -> 检索 -> 复核建议 -> 内容审查 -> 建议卡。

每次运行写入独立 runs/<时间戳>_<名称>/ 目录，重复运行不会覆盖已有输出；
所有步骤记录 JSONL 日志；输出文件附带 SHA-256 清单。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.agents.audit_agent import AuditAgent
from src.agents.profile_agent import ProfileAgent
from src.agents.retrieval_agent import RetrievalAgent
from src.agents.review_agent import ReviewAgent
from src.common.pydantic_schemas import ReviewCard
from src.common.run_log import RunLog, new_run_dir, write_output_manifest
from src.llm.client import get_llm_client


MODULE_VERSIONS = [
    "profile_agent:v0.1",
    "retrieval_agent:v0.1",
    "review_agent:v0-rule-template",
    "audit_agent:v0.1-deterministic",
]


class MinimalPipeline:
    def __init__(self, config: dict[str, Any], root: Path):
        self.config = config
        self.root = Path(root)
        paths = config["paths"]
        self.profile_agent = ProfileAgent(whitelist_path=self.root / paths["whitelist"])
        self.retrieval_agent = RetrievalAgent(
            chunks_path=self.root / paths["knowledge_chunks"],
            mapping_path=self.root / paths["standard_mapping"],
            top_k=config["retrieval"]["top_k"],
            min_score=config["retrieval"]["min_score"],
        )
        self.review_agent = ReviewAgent(
            max_points=config["review"]["max_points"],
            model=config["review"]["model"],
            llm_client=get_llm_client(config),
            use_llm=bool(config["review"].get("use_llm", False)),
        )
        self.audit_agent = AuditAgent(
            forbidden_patterns=config["audit"]["forbidden_patterns"],
            max_rounds=config["audit"]["max_rounds"],
        )

    @staticmethod
    def _leakage_precheck(profile: dict[str, Any]) -> None:
        forbidden = [
            k
            for k in profile
            if k.startswith("future_")
            or k in {"label", "label_available_date", "future_citation_label", "future_citation_categories"}
        ]
        if forbidden:
            raise ValueError(f"输入包含禁止字段（可能泄漏未来信息）：{forbidden}")

    def run(self, profile: dict[str, Any], run_name: str = "e2e") -> dict[str, Any]:
        self._leakage_precheck(profile)
        sample_id = profile.get("sample_id", "UNKNOWN")
        run_dir = new_run_dir(self.root / self.config["paths"]["runs"], run_name, self.config)
        log = RunLog(run_dir / "run_log.jsonl")
        log.log("start", run_id=run_dir.name, sample_id=sample_id)
        try:
            # 1) Profile Agent
            t0 = time.perf_counter()
            profile_out = self.profile_agent.run(profile)
            log.log(
                "module_end",
                module="profile",
                run_id=run_dir.name,
                sample_id=sample_id,
                n_facts=profile_out["n_facts"],
                input_chars=len(json.dumps(profile, ensure_ascii=False)),
                output_chars=len(json.dumps(profile_out["facts"], ensure_ascii=False)),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

            # 2) Retrieval Agent
            t0 = time.perf_counter()
            retrieval = self.retrieval_agent.run(
                profile.get("historical_standard_codes") or [],
                profile.get("historical_risk_categories") or [],
                query_id=sample_id,
            )
            retrieval_dict = retrieval.model_dump()
            log.log(
                "module_end",
                module="retrieval",
                run_id=run_dir.name,
                sample_id=sample_id,
                n_evidence=len(retrieval.items),
                empty_reason=retrieval.empty_reason,
                input_chars=len(json.dumps(profile.get("historical_standard_codes") or [], ensure_ascii=False)),
                output_chars=len(json.dumps(retrieval_dict, ensure_ascii=False)),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

            # 3) Review Agent（阶段1为规则模板占位）
            t0 = time.perf_counter()
            draft = self.review_agent.run(profile, profile_out["facts"], retrieval)
            log.log(
                "module_end",
                module="review",
                run_id=run_dir.name,
                sample_id=sample_id,
                n_points=len(draft["review_points"]),
                input_chars=len(json.dumps({"facts": profile_out["facts"], "retrieval": retrieval_dict}, ensure_ascii=False)),
                output_chars=len(json.dumps(draft, ensure_ascii=False)),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

            # 4) Audit Agent（确定性核对器）
            t0 = time.perf_counter()
            audit = self.audit_agent.run(draft, profile_out["facts"], retrieval, profile)
            audit_dict = audit.model_dump()
            log.log(
                "module_end",
                module="audit",
                run_id=run_dir.name,
                sample_id=sample_id,
                overall_verdict=audit.overall_verdict,
                input_chars=len(json.dumps(draft, ensure_ascii=False)),
                output_chars=len(json.dumps(audit_dict, ensure_ascii=False)),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as exc:  # noqa: BLE001
            log.log("error", run_id=run_dir.name, sample_id=sample_id, error=str(exc))
            log.close()
            raise

        # 5) 组装最终建议卡（方案第15节十项内容）
        card = {
            "sample_id": sample_id,
            "quarter": profile.get("quarter"),
            "ranking_cutoff": profile.get("ranking_cutoff"),
            "frozen_risk": {
                "risk_score": profile.get("risk_score"),
                "risk_percentile": profile.get("risk_percentile"),
                "ranking_source": "M2-frozen" if profile.get("risk_score") is not None else "pending-m2",
                "model_version": profile.get("model_version", ""),
                "score_evidence": profile.get("score_evidence", ""),
            },
            "profile_facts": profile_out["facts"],
            "review_points": draft["review_points"],
            "official_citations": draft["official_citations"],
            "missing_information": draft["missing_information"],
            "evidence_ledger": draft["evidence_ledger"],
            "versions": {
                "modules": MODULE_VERSIONS,
                "model": {
                    "review": draft["model"],
                    "llm_provider": self.config.get("llm", {}).get("provider", "dummy"),
                    "note": self.config["review"]["model_note"],
                },
                "prompts": {"note": "阶段8锁定提示词版本后填写"},
                "knowledge": {"version": "sample-v0.1", "note": "正式知识库等学生2交付"},
            },
            "audit": {
                "status": audit.overall_verdict,
                "attempts": 1,
                "max_attempts": self.config["audit"]["max_rounds"],
                "per_claim": [c.model_dump() for c in audit.claims],
            },
            "final_verdict": audit.overall_verdict,
        }
        # 最终建议卡同样过一遍格式校验（阶段验收1：格式正确）
        ReviewCard.model_validate(card)

        outputs = {
            "profile_facts.json": profile_out["facts"],
            "retrieval.json": retrieval_dict,
            "draft_review.json": draft,
            "audit.json": audit_dict,
            "review_card.json": card,
        }
        written: dict[str, Path] = {}
        for name, obj in outputs.items():
            path = run_dir / name
            path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
            written[name] = path

        manifest_path = write_output_manifest(run_dir, written)
        log.log(
            "end",
            run_id=run_dir.name,
            sample_id=sample_id,
            final_verdict=card["final_verdict"],
            manifest=str(manifest_path.name),
        )
        log.close()

        return {
            "run_dir": str(run_dir),
            "final_verdict": card["final_verdict"],
            "card": card,
            "output_manifest": str(manifest_path),
        }
