"""与 schemas/*.json 对应的 Pydantic 运行时校验模型。

JSON Schema 文件是给人/论文看的契约，Pydantic 模型是程序运行时校验。
两边字段保持一致；字段含义见各 JSON Schema 的描述。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------- 画像
class ProfileCard(BaseModel):
    sample_id: str
    quarter: str
    ranking_cutoff: str
    profile_version: str
    industry_group: Literal["G1", "G2", "G3", "G4", "UNKNOWN"]
    jurisdiction_context: Optional[str] = None
    quarter_number: Optional[int] = Field(default=None, ge=1)
    history_inspections: int = Field(ge=0)
    history_positive_inspections: int = Field(ge=0)
    smoothed_positive_rate: float = Field(ge=0, le=1)
    days_since_last_inspection: Optional[float] = Field(default=None, ge=0)
    days_since_last_positive: Optional[float] = Field(default=None, ge=0)
    inspections_365d: int = Field(ge=0)
    positives_365d: int = Field(ge=0)
    inspections_730d: int = Field(ge=0)
    positives_730d: int = Field(ge=0)
    decayed_inspections: float = Field(ge=0)
    decayed_positives: float = Field(ge=0)
    historical_standard_codes: Optional[list[str]] = None
    historical_risk_categories: Optional[list[str]] = None
    risk_category_counts: Optional[dict[str, int]] = None
    risk_category_unmapped_rate: Optional[float] = Field(default=None, ge=0, le=1)
    risk_score: Optional[float] = None
    risk_percentile: Optional[float] = Field(default=None, ge=0, le=1)
    model_version: Optional[str] = None
    score_evidence: Optional[str] = None
    no_history_flag: bool = False
    no_positive_history_flag: bool = False
    missing_last_inspection_flag: bool = False
    missing_last_positive_flag: bool = False
    entity_match_uncertain_flag: bool = False
    insufficient_evidence_flag: bool = False

    @field_validator("quarter")
    @classmethod
    def quarter_format(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[0-9]{4}Q[1-4]", v):
            raise ValueError(f"quarter 格式应为 YYYYQn，实际为 {v}")
        return v

    @field_validator("historical_risk_categories")
    @classmethod
    def risk_category_format(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        import re

        if v is not None:
            for item in v:
                if not re.fullmatch(r"R[1-9]", item):
                    raise ValueError(f"风险类别格式应为 R1—R9，实际为 {item}")
        return v

    @model_validator(mode="after")
    def check_flags_consistent(self) -> "ProfileCard":
        if self.history_inspections == 0 and not self.no_history_flag:
            raise ValueError("history_inspections=0 时必须置 no_history_flag=True")
        if self.days_since_last_inspection is None and not self.missing_last_inspection_flag:
            raise ValueError("days_since_last_inspection 缺失时必须置 missing_last_inspection_flag=True")
        if self.days_since_last_positive is None and not self.missing_last_positive_flag:
            raise ValueError("days_since_last_positive 缺失时必须置 missing_last_positive_flag=True")
        return self


# ---------------------------------------------------------------- 检索证据
class EvidenceItem(BaseModel):
    evidence_id: str
    document_id: str
    standard_number: str
    section: str
    title: str = ""
    text: str
    source_type: Literal["regulation", "interpretation", "data_definition", "field_manual", "archive"]
    source_url: str
    effective_date: Optional[str] = None
    retrieved_at: str
    is_archived: bool = False
    score: float
    rank: Optional[int] = None


class RetrievalResult(BaseModel):
    query_id: str
    standard_number: str
    risk_categories: list[str] = []
    items: list[EvidenceItem] = []
    empty_reason: Optional[str] = None
    # Stage 9 Canonical Standard 审计字段（可选，默认空；含逐标准 requested/canonical/
    # normalized/status/reason，见 docs/standard_consistency_analysis.md）
    standard_statuses: list[dict[str, Any]] = []


# ---------------------------------------------------------------- 复核建议卡
class FrozenRisk(BaseModel):
    risk_score: Optional[float] = None
    risk_percentile: Optional[float] = Field(default=None, ge=0, le=1)
    ranking_source: str
    model_version: str = ""
    score_evidence: str = ""


class ProfileFact(BaseModel):
    fact_id: str
    statement_zh: str
    field: str
    value: Any
    provenance: str


class ReviewPoint(BaseModel):
    point_id: str
    focus_zh: str
    basis_profile_facts: list[str] = []
    regulation_refs: list[str] = []
    missing_field_info: list[str] = []
    verification_instructions_zh: str


class Citation(BaseModel):
    evidence_id: str
    document_id: str
    section: str
    source_url: str


class MissingInfo(BaseModel):
    field: str
    reason: str


class LedgerEntry(BaseModel):
    claim_id: str
    statement_zh: str
    evidence_refs: list[str] = []
    status: Literal["supported", "unsupported", "deferred"]


class CardVersions(BaseModel):
    modules: list[str] = []
    model: dict[str, Any] = {}
    prompts: dict[str, Any] = {}
    knowledge: dict[str, Any] = {}


class CardAudit(BaseModel):
    status: Literal["PASS", "DEFER", "REJECT"]
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    per_claim: list[Any] = []


class ReviewCard(BaseModel):
    sample_id: str
    quarter: str
    ranking_cutoff: str
    frozen_risk: FrozenRisk
    profile_facts: list[ProfileFact]
    review_points: list[ReviewPoint] = Field(min_length=1, max_length=3)
    official_citations: list[Citation] = []
    missing_information: list[MissingInfo] = []
    evidence_ledger: list[LedgerEntry] = []
    versions: CardVersions
    audit: CardAudit
    final_verdict: Literal["PASS", "DEFER", "REJECT"]


# ---------------------------------------------------------------- 内容审查
class AuditCheck(BaseModel):
    check: Literal[
        "number_consistency",
        "citation_exists",
        "evidence_supports",
        "forbidden_claim",
        "missing_info_hidden",
        "future_info_used",
        "regulation_mix",
    ]
    passed: bool
    detail: str


class AuditClaim(BaseModel):
    claim_id: str
    statement_zh: str
    checks: list[AuditCheck]
    verdict: Literal["PASS", "DEFER", "REJECT"]
    reasons: list[str] = []
    auto_fix_applied: Literal["none", "deleted", "downgraded"] = "none"


class AuditAggregate(BaseModel):
    n_pass: int = Field(ge=0)
    n_defer: int = Field(ge=0)
    n_reject: int = Field(ge=0)
    first_attempt_pass: bool


class AuditResult(BaseModel):
    sample_id: str
    audit_round: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    claims: list[AuditClaim]
    aggregate: AuditAggregate
    overall_verdict: Literal["PASS", "DEFER", "REJECT"]
