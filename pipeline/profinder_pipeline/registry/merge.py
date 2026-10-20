from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .matching import Pick, pick_best
from .models import RegistryRecord
from .textnorm import city_key, has_cjk, norm_en, origin_url, prov_key, registrable_domain


@dataclass(frozen=True)
class Seed:
    name: str
    name_zh: str | None
    domains: tuple[str, ...] = ()
    extra_domains: tuple[str, ...] = ()
    extra_email_domains: tuple[str, ...] = ()
    seed_urls: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Existing:
    id: str
    name: str
    agency_number: str | None = None
    official_url: str | None = None
    province: str | None = None
    city: str | None = None
    openalex_id: str | None = None


@dataclass
class Merged:
    name: str
    name_zh: str | None = None
    name_zh_source: str | None = None
    moe_code: str | None = None
    province: str | None = None
    city: str | None = None
    city_zh: str | None = None
    official_url: str | None = None
    official_url_source: str | None = None
    url_ok: bool | None = None
    openalex_id: str | None = None
    ror_id: str | None = None
    campuschina_key: str | None = None
    sources: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    status: str = "unverified"
    existing_id: str | None = None
    existing_agency_number: str | None = None
    existing_official_url: str | None = None
    existing_official_url_is_placeholder: bool = False
    existing_openalex_id: str | None = None


def load_seed(path: str | Path) -> list[Seed]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out = []
    for u in data["universities"]:
        out.append(
            Seed(
                name=u["name"], name_zh=u.get("nameZh"), domains=tuple(u.get("domains") or ()),
                extra_domains=tuple(u.get("extra_domains") or ()),
                extra_email_domains=tuple(u.get("extra_email_domains") or ()),
                seed_urls=tuple((k, tuple(v or ())) for k, v in (u.get("seed_urls") or {}).items()),
            )
        )
    return out


def _match_campus(seed: Seed, by_norm: dict[str, RegistryRecord], all_recs: list[RegistryRecord]) -> tuple[RegistryRecord | None, str]:
    r = by_norm.get(norm_en(seed.name))
    if r:
        return r, "exact"
    if seed.name_zh:
        for c in all_recs:
            if c.name_zh == seed.name_zh:
                return c, "exact_zh"
    want = norm_en(seed.name)
    best = max(((difflib.SequenceMatcher(None, want, k).ratio(), c) for k, c in by_norm.items()), default=(0.0, None), key=lambda t: t[0])
    if best[1] is not None and best[0] >= 0.93:
        return best[1], "fuzzy"
    return None, "none"


def merge_one(
    seed: Seed,
    moe_idx: dict[str, list[RegistryRecord]],
    campus_by_norm: dict[str, RegistryRecord],
    campus_all: list[RegistryRecord],
    openalex_cands: list[RegistryRecord] | None,
    ror_cands: list[RegistryRecord] | None,
    existing: Existing | None,
    url_checks: dict[str, dict],
) -> Merged:
    m = Merged(name=seed.name)
    flags = m.flags
    used: dict[str, RegistryRecord] = {}

    # --- MOE (authoritative for Chinese name, province, city_zh, school code) ---
    hits = moe_idx.get(seed.name_zh or "", [])
    if len(hits) > 1:
        preferred = [h for h in hits if (h.note or "").startswith("本科")]
        flags.append("moe_ambiguous")
        hits = preferred or hits
    if hits:
        h = hits[0]
        used["moe"] = h
        m.name_zh, m.name_zh_source, m.moe_code, m.city_zh = h.name_zh, "moe_exact", h.external_id, h.city_zh
    else:
        flags.append("moe_no_match")

    # --- CampusChina ---
    cc, how = _match_campus(seed, campus_by_norm, campus_all)
    if cc and how in ("exact", "exact_zh"):
        used["campuschina"] = cc
        m.campuschina_key = cc.external_id
    elif cc:
        flags.append(f"campuschina_fuzzy:{cc.name_en}")   # not counted as a source until a human confirms
    elif campus_all:
        flags.append("no_campuschina_match")

    # --- ROR / OpenAlex (picked from cached search results) ---
    for src, cands in (("ror", ror_cands), ("openalex", openalex_cands)):
        if cands is None:
            flags.append(f"{src}_not_fetched")
            continue
        p: Pick = pick_best(cands, seed.name, seed.name_zh)
        if p.ambiguous:
            flags.append(f"{src}_ambiguous")
        elif p.record is None:
            flags.append(f"no_{src}_match")
        elif p.reason == "fuzzy_only":
            flags.append(f"{src}_fuzzy:{p.record.name_en}")
        else:
            used[src] = p.record

    m.sources = [s for s in ("moe", "ror", "openalex", "campuschina") if s in used]
    if "ror" in used:
        m.ror_id = used["ror"].ror_id
    elif "openalex" in used and used["openalex"].ror_id:
        m.ror_id = used["openalex"].ror_id
    if "openalex" in used:
        m.openalex_id = used["openalex"].external_id

    # --- a Chinese name seen by another registry: the MOE name must be among ALL its Chinese names/aliases ---
    confirmed = ["moe_exact"] if m.name_zh else []
    for s_ in ("ror", "openalex", "campuschina"):
        rec = used.get(s_)
        if rec is None or not m.name_zh:
            continue
        zh_names = sorted({n for n in (rec.name_zh, *rec.aliases) if has_cjk(n)})
        if not zh_names:
            continue
        if m.name_zh in zh_names:
            confirmed.append(s_)
        else:
            flags.append(f"name_zh_conflict:{s_}={'/'.join(zh_names[:3])}")
    if len(confirmed) > 1:
        m.name_zh_source = "+".join(confirmed)

    # --- province / city ---
    prov = {s: used[s].province for s in ("moe", "campuschina", "ror", "openalex") if s in used and used[s].province}
    if len({prov_key(v) for v in prov.values()}) > 1:
        flags.append("province_conflict:" + ",".join(f"{k}={v}" for k, v in prov.items()))
    for s in ("moe", "campuschina", "ror", "openalex"):
        if s in prov:
            m.province = prov[s]
            break
    cities = {s: used[s].city for s in ("campuschina", "ror", "openalex") if s in used and used[s].city}
    if len({city_key(v) for v in cities.values()}) > 1:
        flags.append("city_mismatch:" + ",".join(f"{k}={v}" for k, v in cities.items()))
    for s in ("campuschina", "ror", "openalex"):
        if s in cities:
            m.city = cities[s]
            break

    # --- official URL (precedence ror > openalex > campuschina) ---
    sites = {s: used[s].website for s in ("ror", "openalex", "campuschina") if s in used and used[s].website}
    doms = {s: registrable_domain(v) for s, v in sites.items()}
    if len({d for d in doms.values() if d}) > 1:
        flags.append("url_domain_conflict:" + ",".join(f"{k}={v}" for k, v in doms.items()))
    chosen_src = next((s for s in ("ror", "openalex", "campuschina") if s in sites and doms.get(s)), None)
    if chosen_src:
        url = origin_url(sites[chosen_src])
        m.official_url_source = chosen_src
        chk = url_checks.get(url or "")
        if chk is None:
            flags.append("url_not_checked")
        elif chk.get("status") == 200:
            m.url_ok = True
            final = origin_url(chk.get("final_url") or url)
            if final and registrable_domain(final) == registrable_domain(url):
                url = final
            else:
                flags.append("url_redirects_other_domain")
        else:
            m.url_ok = False
            flags.append(f"url_failed:{chk.get('status') or chk.get('error')}")
        m.official_url = url
    else:
        flags.append("no_official_url")

    # --- existing app row (read-only; used for the report and later for enrich/propose) ---
    if existing:
        m.existing_id, m.existing_agency_number = existing.id, existing.agency_number
        m.existing_official_url = existing.official_url
        m.existing_official_url_is_placeholder = "campuschina.org" in (existing.official_url or "").lower()
        m.existing_openalex_id = existing.openalex_id
    else:
        flags.append("no_existing_row")

    # --- status (doc 02 §4.2) ---
    hard = [f for f in flags if f.split(":")[0] in {"province_conflict", "city_mismatch", "url_domain_conflict", "name_zh_conflict", "moe_no_match", "no_official_url", "url_failed"}]
    if len(m.sources) >= 2 and not hard and m.url_ok is True and m.official_url:
        m.status = "verified"
    elif m.sources:
        m.status = "needs_review"
    else:
        m.status = "unverified"
    return m


def merge_all(
    seeds: list[Seed],
    moe: list[RegistryRecord],
    campus: list[RegistryRecord],
    openalex_by_slug: dict[str, list[RegistryRecord]],
    ror_by_slug: dict[str, list[RegistryRecord]],
    existing: list[Existing],
    url_checks: dict[str, dict],
) -> list[Merged]:
    from .moe import index_by_name

    moe_idx = index_by_name(moe)
    by_norm = {norm_en(c.name_en): c for c in campus if c.name_en}
    ex_by_norm = {norm_en(e.name): e for e in existing}
    out = []
    for s in seeds:
        slug = norm_en(s.name)
        out.append(
            merge_one(s, moe_idx, by_norm, campus, openalex_by_slug.get(slug), ror_by_slug.get(slug), ex_by_norm.get(slug), url_checks)
        )
    return out