"""ingest.candidate as a typed object (doc 01 section 6). Evidence, checks and diffs are lists of small models, never dicts."""
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from ..utils.urls import is_http_url
from .entities import (RECORD_MODELS, ProfessorRecord, ProgramRecord, ScholarshipRecord, UniversityRecord)
from .enums import Action, Decision, EntityType, Extractor, ProposedStatus

PayloadRecord = Union[UniversityRecord, ProfessorRecord, ProgramRecord, ScholarshipRecord]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    quote: str = Field(max_length=500)      # short snippet from the page, for reviewers


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                                # e.g. B1_official_page_200, F_email_domain
    passed: bool
    blocking: bool = True                    # blocking checks gate `verified`; the rest drop a field or raise a soft flag
    detail: Optional[str] = None


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    before: Optional[str] = None             # JSON-encoded value
    after: Optional[str] = None
    kind: Literal["enrich", "propose", "status"] = "enrich"   # Iteration 5: what the loader does with it


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    natural_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    university_name: Optional[str] = None
    payload: PayloadRecord
    source_url: str
    page_id: Optional[str] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    extractor: Extractor
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    checks: list[CheckResult] = Field(default_factory=list)
    proposed_status: ProposedStatus
    decision: Decision
    reject_reason: Optional[str] = None
    matched_entity_id: Optional[str] = None
    action: Optional[Action] = None          # set by the matcher (Iteration 5)
    diff: list[FieldChange] = Field(default_factory=list)
    fetched_at: Optional[datetime] = None

    @field_validator("payload", mode="before")
    @classmethod
    def _payload_matches_entity(cls, v, info: ValidationInfo):
        entity = info.data.get("entity_type")
        model = RECORD_MODELS.get(entity) if entity is not None else None
        if model is None:
            raise ValueError(f"no record model for entity_type {entity!r}")
        if isinstance(v, model):
            return v
        if isinstance(v, dict):
            return model.model_validate(v)
        raise ValueError(f"payload must be a {model.__name__}")

    @field_validator("source_url")
    @classmethod
    def _source_is_http(cls, v):
        if not is_http_url(v):
            raise ValueError("source_url must start with http(s)://")
        return v

    @model_validator(mode="after")
    def _decision_rules(self):
        if self.decision in (Decision.rejected, Decision.human_rejected) and not self.reject_reason:
            raise ValueError("a rejected candidate needs a reject_reason")
        failed_blocking = [c.name for c in self.checks if c.blocking and not c.passed]
        if self.decision == Decision.auto_accept and self.proposed_status == ProposedStatus.verified and failed_blocking:
            raise ValueError(f"cannot auto-accept as verified with failed blocking checks: {failed_blocking}")
        return self
