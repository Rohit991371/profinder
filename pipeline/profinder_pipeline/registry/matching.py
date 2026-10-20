from __future__ import annotations

import difflib
from dataclasses import dataclass

from .models import RegistryRecord
from .textnorm import norm_en


@dataclass(frozen=True)
class Pick:
    record: RegistryRecord | None
    ambiguous: bool = False
    score: float = 0.0
    reason: str = ""


def score_candidate(rec: RegistryRecord, name_en: str, name_zh: str | None) -> float:
    """>=100 = exact English name (or alias) or exact Chinese name. Otherwise a fuzzy score < 60."""
    if rec.country and rec.country.upper() != "CN":
        return 0.0
    want = norm_en(name_en)
    names = [rec.name_en, *rec.aliases]
    keys = {norm_en(n) for n in names if n}
    s = 0.0
    if want and want in keys:
        s += 100
    zh_names = {rec.name_zh, *(a for a in rec.aliases)}
    if name_zh and name_zh in zh_names:
        s += 100
    if s == 0 and want:
        best = max((difflib.SequenceMatcher(None, want, k).ratio() for k in keys if k), default=0.0)
        if best >= 0.9:
            s = best * 60 - 10   # < 60 so it never counts as exact
    return s


def pick_best(cands: list[RegistryRecord], name_en: str, name_zh: str | None) -> Pick:
    scored = sorted(((score_candidate(c, name_en, name_zh), c) for c in cands), key=lambda t: -t[0])
    scored = [t for t in scored if t[0] > 0]
    if not scored:
        return Pick(None, reason="no_candidate")
    top_s, top = scored[0]
    if top_s < 100:
        return Pick(top, ambiguous=False, score=top_s, reason="fuzzy_only")
    same = [c for s, c in scored if s >= top_s - 0.001 and c.external_id != top.external_id]
    if same:
        return Pick(None, ambiguous=True, score=top_s, reason="tie:" + ",".join(sorted({top.external_id or "?", *(c.external_id or "?" for c in same)})))
    return Pick(top, score=top_s, reason="exact")
