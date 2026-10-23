"""Wraps validate/professor_logic.Built into the typed ProfessorRecord + Candidate (doc 01 section 6, doc 02 section 4.1).
Same construction pattern as the Iteration 1 trial (trial/tsinghua.py), generalised for any configured site."""
from datetime import datetime
from typing import Optional

from ..schemas.candidate import Candidate, CheckResult, EvidenceItem
from ..schemas.entities import ProfessorRecord
from ..schemas.enums import Decision, EntityType, Extractor, ProposedStatus
from ..schemas.status import status_pair, status_timestamps
from ..utils.keys import professor_key
from ..utils.placeholders import PlaceholderRegistry
from ..utils.text import normalise_key_text
from .decide import decide
from .professor_logic import Built, Check


def _record(b: Built, proposed: ProposedStatus, now: datetime) -> ProfessorRecord:
    pair = status_pair(EntityType.professor, proposed)
    stamps = status_timestamps(EntityType.professor, proposed, b.fetched_at, now)
    return ProfessorRecord(
        name=b.name, firstName=b.first_name, lastName=b.last_name, nameZh=b.name_zh, position=b.position_en,
        school=b.school, department=b.department, email=b.email, profileUrl=b.profile_url, profileIsPersonal=b.personal,
        personalWebsite=b.personal_website, researchInterests=b.interests, researchKeywords=b.keywords,
        dataSource="official_faculty_page", dataStatus=pair.data_status, verificationStatus=pair.verification_status,
        confidence=b.confidence, researchAreas=b.areas, **stamps)


def to_candidate(b: Built, university: str, registry: PlaceholderRegistry, now: datetime,
                 model: Optional[str] = None, prompt_version: Optional[str] = None) -> Candidate:
    checks = list(b.checks)
    decision, proposed, reason = decide(b.role_class, b.position_raw, checks)
    record = _record(b, proposed, now)
    # B5 runs on what we would store; a placeholder keeps the record out of `verified`.
    placeholders = registry.placeholder_fields("Professor", record.model_dump(mode="json"))
    checks.append(Check("B5_no_placeholders", not placeholders, True, ",".join(placeholders) or None))
    if placeholders:
        decision, proposed, reason = decide(b.role_class, b.position_raw, checks)
        record = _record(b, proposed, now)
    llm_used = b.interests_from_llm
    return Candidate(
        entity_type=EntityType.professor,
        natural_key=professor_key(normalise_key_text(university), b.profile_url, b.personal, b.name_zh,
                                  b.name if b.first_name or b.last_name else None, b.department),
        university_name=university, payload=record, source_url=b.source_url, page_id=b.page_id,
        evidence=[EvidenceItem(field=f, quote=q[:500]) for f, q in b.evidence if q],
        extractor=Extractor.llm if b.extractor == "llm" else Extractor.css,
        model=model if llm_used or b.extractor == "llm" else None,
        prompt_version=prompt_version if llm_used or b.extractor == "llm" else None,
        confidence=b.confidence,
        checks=[CheckResult(name=c.name, passed=c.passed, blocking=c.blocking, detail=c.detail) for c in checks],
        proposed_status=proposed, decision=decision, reject_reason=reason, fetched_at=b.fetched_at)
