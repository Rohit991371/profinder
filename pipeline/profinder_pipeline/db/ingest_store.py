"""Small helpers that write process state into schema `ingest` (run / target / page / candidate)."""
import json
from datetime import datetime
from typing import Any, Optional

from ..schemas.candidate import Candidate
from ..schemas.enums import DiscoveredBy, RenderMode, RunKind, RunStatus, TargetKind
from ..utils.keys import md5_hex, url_hash
from ..utils.urls import canonical_url


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb  # noqa: PLC0415
    return Jsonb(value)


def start_run(conn: Any, kind: RunKind, params: dict[str, Any], git_sha: Optional[str] = None) -> str:
    row = conn.execute("INSERT INTO ingest.run (kind, params, git_sha) VALUES (%s, %s, %s) RETURNING run_id",
                       (kind.value, _jsonb(params), git_sha)).fetchone()
    return str(row[0])


def finish_run(conn: Any, run_id: str, status: RunStatus, stats: dict[str, Any]) -> None:
    conn.execute("UPDATE ingest.run SET finished_at = now(), status = %s, stats = %s WHERE run_id = %s",
                 (status.value, _jsonb(stats), run_id))


def upsert_target(conn: Any, university_name: str, kind: TargetKind, url: str, discovered_by: DiscoveredBy) -> str:
    row = conn.execute(
        "INSERT INTO ingest.target (university_name, kind, url, discovered_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (url, kind) DO UPDATE SET last_fetch_at = now() RETURNING target_id",
        (university_name, kind.value, canonical_url(url), discovered_by.value)).fetchone()
    return str(row[0])


def insert_page(conn: Any, target_id: str, url: str, http_status: Optional[int], render_mode: RenderMode,
                encoding: str, markdown: str, fetched_at: datetime) -> str:
    row = conn.execute(
        "INSERT INTO ingest.page (target_id, url, final_url, url_hash, fetched_at, http_status, render_mode, encoding, content_hash, markdown) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING page_id",
        (target_id, url, url, url_hash(url), fetched_at, http_status, render_mode.value, encoding, md5_hex(markdown), markdown)).fetchone()
    return str(row[0])


def insert_candidate(conn: Any, run_id: str, c: Candidate) -> str:
    dump = c.model_dump(mode="json")
    row = conn.execute(
        "INSERT INTO ingest.candidate (run_id, entity_type, natural_key, university_name, payload, page_id, source_url, evidence, "
        "extractor, model, prompt_version, confidence, checks, proposed_status, decision, reject_reason, matched_entity_id, action, diff) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING candidate_id",
        (run_id, dump["entity_type"], c.natural_key, c.university_name, _jsonb(dump["payload"]), c.page_id, c.source_url,
         _jsonb(dump["evidence"]), dump["extractor"], c.model, c.prompt_version, c.confidence, _jsonb(dump["checks"]),
         dump["proposed_status"], dump["decision"], c.reject_reason, c.matched_entity_id,
         dump["action"], _jsonb(dump["diff"]))).fetchone()
    return str(row[0])


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
