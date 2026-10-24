"""University candidates from the registry result (output/registry/universities_merged.json), so the University matcher has real input.
`status` from the registry merge is the authority (doc 02 section 4.2): verified = >= 2 registries agree and the official URL answered 200."""
from datetime import datetime
from typing import Any, Optional

BLOCKING_NOTE = "registry status decides (doc 02 section 4.2)"
ENGLISH_HOST_LABELS = ("en", "english", "eng", "international", "isc", "oia", "global")


def is_english_subsite(url: str) -> bool:
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    return host.split(".")[0] in ENGLISH_HOST_LABELS


def university_fields(m: dict[str, Any]) -> dict[str, Any]:
    """Pure part: merged registry row -> payload fields, proposed status, decision, checks, evidence, source URL."""
    sources = list(m.get("sources") or [])
    status = m.get("status") or "unverified"
    url_ok = bool(m.get("url_ok")) and bool(m.get("official_url"))
    payload: dict[str, Any] = {"name": m["name"], "nameZh": m.get("name_zh"), "province": m.get("province"), "city": m.get("city"),
                               "openAlexId": m.get("openalex_id"), "dataSource": "registry:" + "+".join(sources) if sources else "registry"}
    if url_ok:                                           # doc 01 section 8.2: officialUrl must answer HTTP 200
        payload["officialUrl"] = m["official_url"]
    proposed = status if status in ("verified", "needs_review", "unverified") else "unverified"
    english_site = url_ok and is_english_subsite(m["official_url"])
    if english_site and proposed == "verified":      # the registry proposed the English sub-site (e.g. en.njtu.edu.cn): a human picks the main site
        proposed = "needs_review"
    checks = [("R_registries_ge_2", len(sources) >= 2, f"sources: {','.join(sources) or 'none'}"),
              ("R_official_url_200", url_ok, m.get("official_url") or "no URL proposed")]
    if english_site:
        checks.append(("S_english_subsite", False, m["official_url"]))
    for flag in m.get("flags") or []:
        checks.append((f"S_flag_{flag}", False, str(flag)))        # informational; the merge status already accounts for conflicts
    decision = "auto_accept" if proposed == "verified" else "needs_review"
    source_url = m.get("official_url") or (f"https://ror.org/{m['ror_id']}" if m.get("ror_id") else
                                           f"https://openalex.org/{m['openalex_id']}" if m.get("openalex_id") else "https://www.campuschina.org/")
    evidence = [("sources", ",".join(sources)), ("official_url", m.get("official_url") or "")]
    return {"payload": payload, "proposed": proposed, "decision": decision, "checks": checks, "evidence": evidence,
            "source_url": source_url, "agency_hint": m.get("existing_agency_number")}


def build_candidate(m: dict[str, Any], now: datetime):
    """Typed Candidate (pydantic)."""
    from ..schemas.candidate import Candidate, CheckResult, EvidenceItem
    from ..schemas.entities import UniversityRecord
    from ..schemas.enums import Decision, EntityType, Extractor, ProposedStatus
    from ..schemas.status import status_pair, status_timestamps
    from ..utils.keys import university_key

    f = university_fields(m)
    proposed = ProposedStatus(f["proposed"])
    pair = status_pair(EntityType.university, proposed)
    stamps = status_timestamps(EntityType.university, proposed, now, now)
    record = UniversityRecord(**f["payload"], dataStatus=pair.data_status, verificationStatus=pair.verification_status, **stamps)
    checks = []
    for name, passed, detail in f["checks"]:
        checks.append(CheckResult(name=name, passed=passed, blocking=name.startswith("R_") and f["decision"] == "auto_accept", detail=detail))
    return Candidate(entity_type=EntityType.university, natural_key=university_key(f["agency_hint"], m["name"]), university_name=m["name"],
                     payload=record, source_url=f["source_url"], evidence=[EvidenceItem(field=a, quote=b[:500]) for a, b in f["evidence"] if b],
                     extractor=Extractor.api, checks=checks, proposed_status=proposed, decision=Decision(f["decision"]),
                     reject_reason=None, fetched_at=now)
