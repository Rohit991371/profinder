"""Entity matchers (task 5.1, doc 01 section 9). Pure Python: existing app rows in, a MatchResult out.

Rule: levels are tried in the documented order. One unique hit at a level = a match. Two or more rows at one level, or two levels
pointing at DIFFERENT rows, = `ambiguous` (the candidate goes to a human; rows are never auto-merged). Weak levels (romanised name +
department) are only used when no stronger level hit anything. Chinese-only names are matched on `nameZh` (the app's own
normalizeName would turn them into an empty string)."""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..utils.text import normalise_key_text
from ..utils.urls import canonical_url


@dataclass(frozen=True)
class MatchResult:
    status: str                         # unique | none | ambiguous
    entity_id: Optional[str] = None
    via: Optional[str] = None           # the level that matched
    ids: tuple[str, ...] = ()           # every row involved (ambiguous)
    note: Optional[str] = None


NO_MATCH = MatchResult("none")


def _url_key(url: Any) -> Optional[str]:
    if not isinstance(url, str) or not url.strip().lower().startswith(("http://", "https://")):
        return None
    try:
        return canonical_url(url)
    except Exception:  # noqa: BLE001
        return None


def _email_key(email: Any) -> Optional[str]:
    return email.strip().lower() if isinstance(email, str) and "@" in email else None


def _text_key(value: Any) -> Optional[str]:
    k = normalise_key_text(value) if isinstance(value, str) else ""
    return k or None


def roman_key(name: Any) -> Optional[str]:
    """Order-insensitive romanised name: 'Zou Qiang' == 'Qiang Zou'. None for non-Latin names."""
    if not isinstance(name, str):
        return None
    tokens = re.findall(r"[a-z]+", unicodedata.normalize("NFKC", name).casefold())
    return " ".join(sorted(tokens)) or None


def openalex_key(value: Any) -> Optional[str]:
    """'https://openalex.org/I20231570' and 'I20231570' are the same institution."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().rstrip("/").rsplit("/", 1)[-1].upper() or None


def _index(rows: Iterable[dict], fns: dict[str, Callable[[dict], Any]]) -> dict[str, dict[Any, list[str]]]:
    out: dict[str, dict[Any, list[str]]] = {name: {} for name in fns}
    for row in rows:
        rid = str(row["id"])
        for name, fn in fns.items():
            key = fn(row)
            if key is not None:
                bucket = out[name].setdefault(key, [])
                if rid not in bucket:
                    bucket.append(rid)
    return out


def resolve(levels: list[tuple[str, Any, dict[Any, list[str]], bool]]) -> MatchResult:
    """levels = [(name, candidate_key, index_for_that_level, weak)] in priority order."""
    found: Optional[tuple[str, str]] = None
    for name, key, index, weak in levels:
        if key is None:
            continue
        if weak and found is not None:
            continue
        ids = index.get(key, [])
        if not ids:
            continue
        if len(ids) > 1:
            return MatchResult("ambiguous", None, name, tuple(ids), f"{len(ids)} existing rows share this {name}")
        if found is not None and found[1] != ids[0]:
            return MatchResult("ambiguous", None, name, (found[1], ids[0]), f"{found[0]} and {name} point to different rows")
        if found is None:
            found = (name, ids[0])
    if found:
        return MatchResult("unique", found[1], found[0], (found[1],))
    return NO_MATCH


# ------------------------------------------------------------------------------------------------ university
class UniversityMatcher:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.idx = _index(rows, {
            "openAlexId": lambda r: openalex_key(r.get("openAlexId")),
            "agencyNumber": lambda r: (r.get("agencyNumber") or "").strip() or None,
            "name": lambda r: (r.get("name") or "").strip().casefold() or None,
            "name_normalised": lambda r: _text_key(r.get("name")),
            "nameZh": lambda r: _text_key(r.get("nameZh")),
        })
        self.by_id = {str(r["id"]): r for r in rows}

    def match(self, payload: dict) -> MatchResult:
        name = (payload.get("name") or "").strip()
        return resolve([
            ("openAlexId", openalex_key(payload.get("openAlexId")), self.idx["openAlexId"], False),
            ("agencyNumber", (payload.get("agencyNumber") or "").strip() or None, self.idx["agencyNumber"], False),
            ("name", name.casefold() or None, self.idx["name"], False),
            ("name_normalised", _text_key(name), self.idx["name_normalised"], False),
            ("nameZh", _text_key(payload.get("nameZh")), self.idx["nameZh"], False),
        ])

    def id_for_name(self, name: str) -> Optional[str]:
        """Resolve the university a candidate belongs to by its (seed) name: exact, then normalised. Ambiguity -> None."""
        for idx in (self.idx["name"], self.idx["name_normalised"]):
            key = name.strip().casefold() if idx is self.idx["name"] else _text_key(name)
            ids = idx.get(key, []) if key else []
            if len(ids) == 1:
                return ids[0]
            if len(ids) > 1:
                return None
        return None


# ------------------------------------------------------------------------------------------------ professor
class ProfessorMatcher:
    """Existing professors of ONE university."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.idx = _index(rows, {
            "profileUrl": lambda r: _url_key(r.get("profileUrl")),
            "email": lambda r: _email_key(r.get("email")),
            "nameZh": lambda r: _text_key(r.get("nameZh")),
            "name_department": lambda r: ((roman_key(r.get("name")), _text_key(r.get("department")))
                                          if roman_key(r.get("name")) and _text_key(r.get("department")) else None),
            "name_only": lambda r: roman_key(r.get("name")),
        })

    def match(self, payload: dict) -> MatchResult:
        rk, dept = roman_key(payload.get("name")), _text_key(payload.get("department"))
        url = _url_key(payload.get("profileUrl")) if payload.get("profileIsPersonal") else None
        res = resolve([
            ("profileUrl", url, self.idx["profileUrl"], False),
            ("email", _email_key(payload.get("email")), self.idx["email"], False),
            ("nameZh", _text_key(payload.get("nameZh")), self.idx["nameZh"], False),
            ("name_department", (rk, dept) if rk and dept else None, self.idx["name_department"], True),
        ])
        if res.status == "none" and rk:
            n = len(self.idx["name_only"].get(rk, []))
            if n:   # information for the reviewer only: never merged automatically (Wang Wei is not one person)
                return MatchResult("none", None, None, (), f"same_romanised_name_rows={n} (not merged)")
        return res


# ------------------------------------------------------------------------------------------------ program / scholarship
class ProgramMatcher:
    def __init__(self, rows: list[dict]):
        self.idx = _index(rows, {
            "degree_major": lambda r: ((_text_key(r.get("degree")), _text_key(r.get("major")))
                                       if _text_key(r.get("degree")) and _text_key(r.get("major")) else None),
            "programUrl": lambda r: _url_key(r.get("programUrl")),
        })

    def match(self, payload: dict) -> MatchResult:
        d, m = _text_key(payload.get("degree")), _text_key(payload.get("major"))
        return resolve([
            ("degree_major", (d, m) if d and m else None, self.idx["degree_major"], False),
            ("programUrl", _url_key(payload.get("programUrl")), self.idx["programUrl"], False),
        ])


class ScholarshipMatcher:
    def __init__(self, rows: list[dict]):
        self.idx = _index(rows, {"name": lambda r: _text_key(r.get("name")), "officialUrl": lambda r: _url_key(r.get("officialUrl"))})

    def match(self, payload: dict) -> MatchResult:
        return resolve([
            ("name", _text_key(payload.get("name")), self.idx["name"], False),
            ("officialUrl", _url_key(payload.get("officialUrl")), self.idx["officialUrl"], False),
        ])
