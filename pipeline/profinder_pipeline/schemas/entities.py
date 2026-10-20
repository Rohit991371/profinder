"""Strict record models. Field names ARE the Prisma column names (doc 01 section 8), so a release JSONL maps 1:1 to the
app tables. No dict / List[dict] fields (decision D15). `id`, `createdAt`, `updatedAt` are supplied by the loader."""
import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..utils.emails import is_valid_email
from ..utils.text import is_cjk_only
from ..utils.urls import is_http_url
from .enums import DataStatus, EntityType, VerificationStatus, RESEARCH_AREAS
from .status import is_consistent

ResearchAreaName = Literal[RESEARCH_AREAS]  # type: ignore[valid-type]  # tuple -> Literal of 24 names
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

URL_FIELDS = ("officialUrl", "sourceUrl", "applicationUrl", "scholarshipUrl", "profileUrl", "personalWebsite", "programUrl", "logoUrl")
DATE_FIELDS = ("applicationDeadline", "scholarshipDeadline", "deadline", "openingDate")


def check_iso_date(value: str) -> str:
    """Exact calendar date as YYYY-MM-DD, or ValueError. Vague text ('usually March') must never be stored (D7)."""
    if not _ISO_DATE_RE.match(value):
        raise ValueError("date must be an exact ISO date YYYY-MM-DD (vague/typical dates are never stored)")
    date.fromisoformat(value)
    return value


class RecordBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator(*URL_FIELDS, mode="after", check_fields=False)
    @classmethod
    def _urls_are_http(cls, v):
        if v is not None and not is_http_url(v):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator(*DATE_FIELDS, mode="after", check_fields=False)
    @classmethod
    def _dates_are_exact(cls, v):
        return None if v is None else check_iso_date(v)


class UniversityRecord(RecordBase):
    name: str
    nameZh: Optional[str] = None
    country: str = "China"
    province: Optional[str] = None
    city: Optional[str] = None
    officialUrl: Optional[str] = None
    sourceUrl: Optional[str] = None
    description: Optional[str] = None
    agencyNumber: Optional[str] = None
    openAlexId: Optional[str] = None
    logoUrl: Optional[str] = None
    dataStatus: Optional[DataStatus] = None
    dataSource: Optional[str] = None
    applicationUrl: Optional[str] = None
    applicationFee: Optional[str] = None
    applicationDeadline: Optional[str] = None
    scholarshipUrl: Optional[str] = None
    scholarshipDeadline: Optional[str] = None
    verifiedAt: Optional[datetime] = None
    verificationStatus: Optional[VerificationStatus] = None
    lastCheckedAt: Optional[datetime] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _statuses_consistent(self):
        if not is_consistent(EntityType.university, self.dataStatus, self.verificationStatus):
            raise ValueError("dataStatus and verificationStatus disagree")
        return self


class ProfessorRecord(RecordBase):
    name: str                                    # romanised 'Family Given'
    universityId: Optional[str] = None           # resolved at match time
    firstName: Optional[str] = None              # given
    lastName: Optional[str] = None               # family
    nameZh: Optional[str] = None
    position: Optional[str] = None               # normalised English
    school: Optional[str] = None
    department: Optional[str] = None
    country: Optional[str] = None
    email: Optional[str] = None
    profileUrl: Optional[str] = None
    personalWebsite: Optional[str] = None
    researchInterests: Optional[str] = None
    researchKeywords: Optional[str] = None
    lab: Optional[str] = None
    publications: Optional[str] = None
    publicationCount: Optional[int] = Field(default=None, ge=0)
    orcid: Optional[str] = None
    openAlexId: Optional[str] = None
    semanticScholarId: Optional[str] = None
    profileIsPersonal: bool = False
    dataStatus: Optional[DataStatus] = None
    dataSource: Optional[str] = None
    lastSyncedAt: Optional[datetime] = None
    verifiedAt: Optional[datetime] = None
    verificationStatus: Optional[VerificationStatus] = None
    lastCheckedAt: Optional[datetime] = None
    lastVerifiedAt: Optional[datetime] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    collegeId: Optional[str] = None
    departmentId: Optional[str] = None
    # Link rows (ProfessorResearchArea), not a Prisma column. Only the app's 24 names are possible; 0 is allowed.
    researchAreas: list[ResearchAreaName] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def _email_regex(cls, v):
        if v is not None:
            if not is_valid_email(v):
                raise ValueError("invalid email")
            v = v.lower()
        return v

    @field_validator("position")
    @classmethod
    def _position_is_english(cls, v):
        if v is not None and is_cjk_only(v):
            raise ValueError("position must be normalised English, not CJK-only")
        return v

    @field_validator("researchKeywords")
    @classmethod
    def _max_ten_keywords(cls, v):
        if v is not None and len([k for k in re.split(r"[,;，；]", v) if k.strip()]) > 10:
            raise ValueError("researchKeywords holds at most 10 keywords")
        return v

    @model_validator(mode="after")
    def _cross_checks(self):
        if self.profileIsPersonal and not self.profileUrl:
            raise ValueError("profileIsPersonal=true requires a profileUrl")
        if not is_consistent(EntityType.professor, self.dataStatus, self.verificationStatus):
            raise ValueError("dataStatus and verificationStatus disagree")
        return self


class ProgramRecord(RecordBase):
    universityId: Optional[str] = None
    degree: Literal["Master", "PhD"]
    major: str
    officialName: Optional[str] = None
    teachingLang: Optional[Literal["English", "Chinese", "English and Chinese"]] = None
    requirements: Optional[str] = None
    programUrl: Optional[str] = None
    sourceUrl: Optional[str] = None
    applicationUrl: Optional[str] = None
    duration: Optional[str] = None
    tuition: Optional[str] = None
    applicationFee: Optional[str] = None
    deadline: Optional[str] = None
    openingDate: Optional[str] = None
    scholarshipDeadline: Optional[str] = None
    englishReq: Optional[str] = None
    ielts: Optional[str] = None
    toefl: Optional[str] = None
    gpaRequirement: Optional[str] = None
    cscType: Optional[str] = None
    supervisorRequired: Optional[str] = None
    studyMode: Optional[str] = None
    degreeLevel: Optional[str] = None
    verificationStatus: Optional[VerificationStatus] = None
    lastCheckedAt: Optional[datetime] = None
    lastVerifiedAt: Optional[datetime] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class ScholarshipRecord(RecordBase):
    universityId: Optional[str] = None
    name: str
    type: Optional[Literal["CSC", "University", "Presidential"]] = None
    requirements: Optional[str] = None
    deadline: Optional[str] = None
    officialUrl: Optional[str] = None
    advantages: Optional[str] = None
    coverage: Optional[Literal["Full", "Partial", "Unknown"]] = None
    dataStatus: Optional[DataStatus] = None
    verificationStatus: Optional[VerificationStatus] = None
    lastCheckedAt: Optional[datetime] = None
    lastVerifiedAt: Optional[datetime] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _statuses_consistent(self):
        if not is_consistent(EntityType.scholarship, self.dataStatus, self.verificationStatus):
            raise ValueError("dataStatus and verificationStatus disagree")
        return self


RECORD_MODELS: dict[EntityType, type[RecordBase]] = {
    EntityType.university: UniversityRecord,
    EntityType.professor: ProfessorRecord,
    EntityType.program: ProgramRecord,
    EntityType.scholarship: ScholarshipRecord,
}
# Prisma model name for each entity (used by the contract test, the placeholder registry and the loader).
PRISMA_MODEL = {
    EntityType.university: "University", EntityType.professor: "Professor",
    EntityType.program: "Program", EntityType.scholarship: "Scholarship",
}
# Model fields that are NOT columns of the Prisma model.
NON_COLUMN_FIELDS = {EntityType.professor: frozenset({"researchAreas"})}
# Prisma columns the loader/DB fill itself. Everything else a table has must be a model field or be listed in IGNORED_COLUMNS.
LOADER_SUPPLIED_COLUMNS = frozenset({"id", "createdAt", "updatedAt"})
# Prisma columns deliberately left to other tools (doc 01 section 8.3: taxonomy links come from `npm run sync:academic`).
IGNORED_COLUMNS = {
    EntityType.program: frozenset({"academicFieldId", "disciplineId", "majorId", "collegeId", "departmentId"}),
}
