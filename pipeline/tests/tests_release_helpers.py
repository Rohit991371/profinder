"""Shared candidate factory for the release/review tests."""
from datetime import datetime, timezone

T = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


def cand(i, entity="professor", action="insert", decision="auto_accept", payload=None, diff=None, **kw):
    c = {"candidate_id": f"c{i:03d}", "entity_type": entity, "university_name": "Tsinghua University", "natural_key": f"k{i}",
         "payload": payload if payload is not None else {"name": f"Name{i}", "nameZh": f"姓{i}", "email": None, "position": "Professor", "universityId": "u1",
                                                       "dataStatus": "verified", "verificationStatus": "VERIFIED", "researchAreas": ["Machine Learning"]},
         "source_url": "https://www.cs.tsinghua.edu.cn/info/1/1.htm", "evidence": [], "checks": [], "proposed_status": "verified", "decision": decision,
         "matched_entity_id": None, "action": action, "diff": diff or [], "confidence": None, "fetched_at": T, "http_status": 200,
         "content_hash": "abc", "release_status": None, "qa_sample": False, "qa_result": None, "reject_reason": None, "release_id": None}
    c.update(kw)
    return c
