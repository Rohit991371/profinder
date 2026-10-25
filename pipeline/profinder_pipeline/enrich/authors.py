"""OpenAlex authors: parsing, the strong-match rule, and the (cached) HTTP search (task 6.1, doc 01 S7b, decision D9).

D9: OpenAlex NEVER creates a professor. It only fills ids on an EXISTING official-sourced professor, and only on a strong match:
  exact name (order-insensitive, Latin) + the university's OpenAlex institution among the author's affiliations + exactly one such
  author + a field concordance (the author's topics belong to one of the allowed fields). Anything weaker is listed for a human.
The response shape below is taken from the OpenAlex docs, NOT from a response seen live: `enrich probe` is the check, and the parser
reads every key defensively (a missing key can only make a match weaker, never stronger).
Pure Python except `search_authors` (needs a client)."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..match.matchers import openalex_key, roman_key

BASE = "https://api.openalex.org/authors"
DEFAULT_FIELDS = ("Computer Science", "Engineering", "Mathematics", "Decision Sciences")


@dataclass(frozen=True)
class Author:
    openalex_id: str                     # short form, e.g. A5023888391
    name: str
    alt_names: tuple = ()
    orcid: Optional[str] = None          # bare 0000-0000-0000-0000
    works_count: Optional[int] = None
    cited_by: Optional[int] = None
    institution_ids: frozenset = frozenset()   # short ids, e.g. I20231570 (current + past affiliations)
    fields: frozenset = frozenset()      # field names of the author's topics

    @property
    def url(self) -> str:
        return f"https://openalex.org/{self.openalex_id}"


def short_id(v: Any) -> Optional[str]:
    return openalex_key(v)


def bare_orcid(v: Any) -> Optional[str]:
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dXx])", v) if isinstance(v, str) else None
    return m.group(1).upper() if m else None


def _inst_ids(o: dict) -> frozenset:
    out = set()
    for inst in (o.get("last_known_institutions") or []):
        if isinstance(inst, dict) and short_id(inst.get("id")):
            out.add(short_id(inst["id"]))
    one = o.get("last_known_institution")
    if isinstance(one, dict) and short_id(one.get("id")):
        out.add(short_id(one["id"]))
    for a in (o.get("affiliations") or []):
        inst = a.get("institution") if isinstance(a, dict) else None
        if isinstance(inst, dict) and short_id(inst.get("id")):
            out.add(short_id(inst["id"]))
    return frozenset(out)


def _fields(o: dict) -> frozenset:
    out = set()
    for t in (o.get("topics") or []):
        f = t.get("field") if isinstance(t, dict) else None
        if isinstance(f, dict) and f.get("display_name"):
            out.add(str(f["display_name"]))
    return frozenset(out)


def parse_author(o: dict) -> Optional[Author]:
    aid = short_id(o.get("id"))
    if not aid or not o.get("display_name"):
        return None
    wc, cb = o.get("works_count"), o.get("cited_by_count")
    return Author(aid, str(o["display_name"]), tuple(str(a) for a in (o.get("display_name_alternatives") or []) if a),
                  bare_orcid(o.get("orcid")), wc if isinstance(wc, int) else None, cb if isinstance(cb, int) else None,
                  _inst_ids(o), _fields(o))


def parse_response(data: dict) -> list:
    return [a for a in (parse_author(x) for x in (data.get("results") or []) if isinstance(x, dict)) if a]


# ------------------------------------------------------------------------------------------------ the strong-match rule
@dataclass
class MatchOutcome:
    status: str                          # strong | review | none | skip
    reason: str
    author: Optional[Author] = None
    others: list = field(default_factory=list)   # other authors that also passed name (+ institution): ambiguous
    checks: dict = field(default_factory=dict)


def name_matches(prof_name: str, author: Author) -> bool:
    """Order-insensitive Latin comparison ('Zou Qiang' == 'Qiang Zou'); alternatives count, initials do not."""
    k = roman_key(prof_name)
    return bool(k) and any(roman_key(n) == k for n in (author.name, *author.alt_names))


def decide(prof: dict, authors: list, inst_id: Optional[str], allowed_fields: tuple = DEFAULT_FIELDS) -> MatchOutcome:
    """prof: existing app row (name, orcid, openAlexId). inst_id: short OpenAlex id of the professor's university."""
    if prof.get("openAlexId"):
        return MatchOutcome("skip", "already_has_openalex_id")
    if not inst_id:
        return MatchOutcome("skip", "university_has_no_openalex_id")
    if not roman_key(prof.get("name") or ""):
        return MatchOutcome("skip", "no_latin_name")
    named = [a for a in authors if name_matches(prof["name"], a)]
    if not named:
        return MatchOutcome("none", "no_exact_name_match", checks={"candidates": len(authors)})
    at_inst = [a for a in named if inst_id in a.institution_ids]
    checks = {"name_matches": len(named), "institution_matches": len(at_inst)}
    if not at_inst:
        return MatchOutcome("review", "name_matches_but_not_at_this_university", named[0], named[1:], checks)
    if len(at_inst) > 1:
        return MatchOutcome("review", "several_authors_with_same_name_at_university", at_inst[0], at_inst[1:], checks)
    a = at_inst[0]
    own = bare_orcid(prof.get("orcid"))
    if own and a.orcid and own != a.orcid:
        return MatchOutcome("review", "orcid_conflict", a, [], {**checks, "orcid_conflict": True})
    if not a.works_count:
        return MatchOutcome("review", "author_without_works", a, [], checks)
    concord = bool(a.fields & set(allowed_fields))
    checks["field_concordance"] = concord
    if own and a.orcid == own:
        return MatchOutcome("strong", "orcid_equal", a, [], checks)
    if not concord:
        return MatchOutcome("review", "fields_do_not_match_allowed_fields" if a.fields else "no_topics_to_check_fields", a, [], checks)
    return MatchOutcome("strong", "name_institution_unique_field_concordant", a, [], checks)


# ------------------------------------------------------------------------------------------------ http (cached)
def query_name(prof_name: str) -> str:
    """'Family Given' (our convention) -> 'Given Family' (how OpenAlex usually writes Chinese names)."""
    parts = re.findall(r"[A-Za-z][A-Za-z'\-]*", prof_name or "")
    return " ".join(parts[1:] + parts[:1]) if len(parts) > 1 else " ".join(parts)


def cache_file(cache_dir: Path, inst_id: str, q: str) -> Path:
    return cache_dir / "openalex_authors" / f"{inst_id}_{hashlib.md5(q.casefold().encode()).hexdigest()}.json"


def search_authors(client, q: str, inst_id: str, cache_dir: Path, api_key: Optional[str], per_page: int = 10,
                   refresh: bool = False, pause: float = 0.2, get_json=None) -> tuple:
    """-> (authors, network_call_made). Raw JSON is cached (the key never is). Filter syntax is from the docs: see `enrich probe`."""
    p = cache_file(cache_dir, inst_id, q)
    if p.exists() and not refresh:
        return parse_response(json.loads(p.read_text(encoding="utf-8"))), False
    if get_json is None:
        from ..registry.openalex import http_get_json as get_json   # noqa: PLC0415 - retries, back-off, Retry-After
    params = {"search": q, "filter": f"last_known_institutions.id:{inst_id}", "per-page": str(per_page)}
    if api_key:
        params["api_key"] = api_key
    data = get_json(client, BASE, params=params)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    time.sleep(pause)
    return parse_response(data), True
