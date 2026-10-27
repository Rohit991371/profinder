"""site-learn (task 4.3): propose a config/sites/<slug>.yml for a faculty list the pipeline has never seen.
Groq (or, with --no-llm, a plain heuristic) proposes CSS selectors ONCE; the proposal is then validated by really running it on
the page, written to config/sites/_proposed/<slug>.yml with `needs_review: true`, and a human reviews it. Nothing proposed is used
by `extract run` until that flag is removed. The LLM sees only a small skeleton (two example items per candidate group)."""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import yaml
from bs4 import Tag

from ..utils.text import CJK_RE, clean_name
from .css import classify_href, parse_list, soup_of
from .emailtools import find_emails_loose
from .sitecfg import SiteConfig

ITEM_TAGS = ("li", "tr", "dl", "dd", "div", "article", "a")
# menus, footers and breadcrumbs repeat just like people lists; groups inside them are heavily down-weighted
NAV_TOKENS = {"nav", "navbar", "menu", "footer", "breadcrumb", "breadcrumbs", "crumb", "header", "topbar", "sidebar", "side", "sub", "subnav",
              "dropdown", "tabs"}
NAV_WORDS = {"introduction", "program", "programs", "class", "classes", "teachers", "teacher", "news", "about", "contact", "home", "education",
             "research", "undergraduate", "postgraduate", "graduate", "admissions", "people", "faculty", "professor", "professors", "staff", "overview",
             "resources", "links", "students", "alumni", "events", "announcements", "college", "school", "department", "institute", "center", "centre",
             "laboratory", "lab", "library", "services", "international", "cooperation", "culture", "party", "administration", "career", "employment",
             "teaching", "academic", "science", "engineering", "technology"}
LABELS = ("职称", "职务", "Title", "Position")


@dataclass
class Group:
    selector: str
    count: int
    examples: list[Tag]


def _comp(el: Tag) -> str:
    cls = sorted(c for c in (el.get("class") or []) if c)
    return el.name + ("." + ".".join(cls) if cls else "")


def _signature(el: Tag) -> tuple[str, ...]:
    chain, cur = [], el
    while isinstance(cur, Tag) and cur.name not in ("[document]", "html"):
        chain.append(_comp(cur))
        cur = cur.parent
    return tuple(reversed(chain))


def _selector(sig: tuple[str, ...]) -> str:
    """Nearest classed ancestor + the item component, e.g. ('...','div.people01-nr','dl','dd','ul','li') -> 'div.people01-nr li'."""
    item = sig[-1]
    for comp in reversed(sig[:-1]):
        if "." in comp:
            return f"{comp} {item}"
    return item


def _is_navish(sig: tuple[str, ...]) -> bool:
    for comp in sig:
        tag, *classes = comp.split(".")
        if tag in ("nav", "header", "footer") or any(t in NAV_TOKENS for c in classes for t in re.split(r"[-_]", c.lower())):
            return True
    return False


def _has_text_and_link(el: Tag) -> bool:
    txt = el.get_text(" ", strip=True)
    return 2 <= len(txt) <= 500 and (el.find("a", href=True) is not None or el.name == "a" or bool(find_emails_loose(txt)))


def find_repeated_groups(html: str, min_count: int = 8, top: int = 3) -> list[Group]:
    soup = soup_of(html)
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    body = soup.body or soup
    buckets: dict[tuple[str, ...], list[Tag]] = defaultdict(list)
    for el in body.find_all(ITEM_TAGS):
        if _has_text_and_link(el):
            buckets[_signature(el)].append(el)
    scored: list[tuple[float, Group]] = []
    for sig, els in buckets.items():
        if len(els) < min_count:
            continue
        avg = sum(len(e.get_text(" ", strip=True)) for e in els) / len(els)
        cjk = sum(1 for e in els if CJK_RE.search(e.get_text()))
        nested = sum(1 for e in els if e.find(sig[-1].split(".")[0]) is not None and e.name != "a")
        score = len(els) * (1.0 if 4 <= avg <= 300 else 0.3) * (0.5 + cjk / len(els)) * (0.3 if nested > len(els) / 2 else 1.0)
        if _is_navish(sig):
            score *= 0.15
        scored.append((score, Group(_selector(sig), len(els), els[:2])))
    scored.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[Group] = []
    for _s, g in scored:
        if g.selector not in seen:
            seen.add(g.selector)
            out.append(g)
        if len(out) >= top:
            break
    return out


def _compact(el: Tag, limit: int = 1100) -> str:
    keep = ("class", "href", "data-name", "name")
    def one(e: Any) -> str:
        if not isinstance(e, Tag):
            return re.sub(r"\s+", " ", str(e)).strip()
        attrs = "".join(f' {k}="{re.sub(r"[0-9]+", "#", " ".join(v) if isinstance(v, list) else v)[:60]}"' for k, v in e.attrs.items() if k in keep)
        return f"<{e.name}{attrs}>{''.join(one(c) for c in e.children)}</{e.name}>"
    return re.sub(r"\s{2,}", " ", one(el))[:limit]


def skeleton_for_llm(groups: list[Group]) -> str:
    parts = []
    for i, g in enumerate(groups, 1):
        parts.append(f"CANDIDATE {i}: selector `{g.selector}` matches {g.count} elements. Two examples:\n" + "\n".join(_compact(e) for e in g.examples))
    return "\n\n".join(parts)


def _name_ok(text: Optional[str]) -> bool:
    """A person's name: 2-6 Chinese characters, or 2-4 capitalised Latin words none of which is a menu/section word."""
    if not text:
        return False
    zh, _note = clean_name(text)
    if not zh:
        return False
    if re.fullmatch(r"[\u3400-\u9fff·]{2,6}", zh):
        return True
    if re.fullmatch(r"[A-Z][A-Za-z'’-]+(?: [A-Z][A-Za-z'’-]+){1,3}", zh):
        return not any(w.lower() in NAV_WORDS for w in zh.split())
    return False


def heuristic_fields(html: str, item_css: str, item_is_anchor: bool = False) -> dict[str, Any]:
    """Pick name/link/position selectors by trying a few likely ones against the real items and keeping the best."""
    if item_is_anchor:                         # the item itself is the person link (SJTU: <a href=...>name</a>)
        return {"name": {"css": ":scope"}, "profile_href": {"css": ":scope", "attr": "href"}, "emails": {"emails": True}}
    soup = soup_of(html)
    items = soup.select(item_css)[:60]
    best_name, best_rate = "a", -1.0
    for css in ("h1 a", "h2 a", "h3 a", "h4 a", "p a", "a", "h2", "h3", "strong", "span"):
        texts = [(e.select_one(css).get_text(" ", strip=True) if e.select_one(css) else None) for e in items]
        rate = sum(_name_ok(t) for t in texts) / max(len(items), 1)
        if rate > best_rate + 0.05:
            best_name, best_rate = css, rate
    fields: dict[str, Any] = {"name": {"css": best_name}}
    if best_name.endswith("a"):
        fields["profile_href"] = {"css": best_name, "attr": "href"}
    else:
        fields["profile_href"] = {"css": "a", "attr": "href"}
    text_items = ["\n".join(e.stripped_strings) for e in items]
    for label in LABELS:
        if sum(bool(re.search(label + r"[：:]\s*\S", t)) for t in text_items) >= 0.5 * max(len(items), 1):
            fields["position"] = {"label": label + r"[：:]\s*([^\n$]*)"}
            break
    fields["emails"] = {"emails": True}
    return fields


def build_site_dict(university: str, list_url: str, item_css: str, fields: dict[str, Any], patterns: list[str], proposed_by: str,
                    stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "needs_review": True, "proposed_by": proposed_by, "proposal_stats": stats,
        "university": university, "list_url": list_url, "school_en": None, "school_zh": None, "render": "static",
        "personal_url_patterns": patterns,
        "list": {"item": item_css, "min_items": max(1, int(0.7 * stats.get("people", 1))), "fields": fields},
        "profile": {"required": False, "fields": {}},
    }


def url_patterns_from_samples(hrefs: list[str]) -> list[str]:
    """Generalise the observed person links: digits -> \\d+; when every person has a different slug in the same folder
    (…/jiaoshiml/chenhaibo.html) fall back to folder + any single file name. Most common pattern first. A human reviews it."""
    exact: Counter[str] = Counter()
    folders: Counter[str] = Counter()
    for h in hrefs:
        m = re.match(r"(https?://[^/?#]+)(/[^?#]*)?", h or "")
        if not m:
            continue
        path = m.group(2) or ""
        norm = re.sub(r"\d+", "\x00", re.escape(path)).replace("\x00", "\\d+")
        exact[re.escape(m.group(1)) + norm + "$"] += 1
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        folders[re.escape(m.group(1) + folder) + r"/[^/]+$"] += 1
    if len(exact) > 5 and len(folders) <= 3:
        return [p for p, _n in folders.most_common(3)]
    if len(exact) > 5:
        seg = _by_segments(hrefs)
        if seg:
            return seg
    return [p for p, _n in exact.most_common(3)]


def _by_segments(hrefs: list[str]) -> list[str]:
    """Teacher portals give every person their own folder (https://faculty.x.edu.cn/anying/zh_CN/index.htm). Exact patterns would then match
    only the three most common people. Generalise per path segment: a position whose value differs between people becomes `[^/?#]+`,
    a position that is the same for (nearly) everybody stays literal. Returns [] unless the result is at most 3 patterns that together
    still match every sample."""
    groups: dict[tuple, list[list[str]]] = {}
    for h in hrefs:
        m = re.match(r"(https?)://([^/?#]+)(/[^?#]*)?", h or "")
        if not m:
            continue
        segs = [x for x in (m.group(3) or "").split("/") if x]
        groups.setdefault(("https?", m.group(2), len(segs)), []).append(segs)     # http and https links to one portal are the same person page
    patterns: list[str] = []
    for (scheme, host, n), rows in groups.items():
        parts = []
        for i in range(n):
            vals = {r[i] for r in rows}
            parts.append(re.escape(rows[0][i]) if len(vals) == 1 else r"[^/?#]+")
        prefix = scheme + re.escape("://" + host)
        patterns.append(prefix + "".join("/" + p for p in parts) + "$" if n else prefix + "/?$")
    patterns = sorted(set(patterns), key=lambda p: -len(p))[:3]
    ok = all(any(re.match(p, h) for p in patterns) for h in hrefs if h)
    return patterns if ok and len(patterns) <= 3 else []


def validate_proposal(html: str, cfg: SiteConfig) -> dict[str, Any]:
    r = parse_list(html, cfg)
    n = len(r.people)
    pct = lambda k: round(sum(1 for p in r.people if k(p)) / n, 2) if n else 0.0  # noqa: E731
    sample = [{"name": p.name_raw, "position": p.position_raw, "email": (p.emails or [None])[0], "href": p.profile_href} for p in r.people[:5]]
    stats = {"people": n, "raw_items": r.raw_items, "names_ok": pct(lambda p: _name_ok(p.name_raw)), "with_link": pct(lambda p: bool(p.profile_href)),
             "with_position": pct(lambda p: bool(p.position_raw)), "with_email": pct(lambda p: bool(p.emails)), "sample": sample}
    stats["verdict"] = "plausible" if n >= 5 and stats["names_ok"] >= 0.8 else "poor: these do not look like people (menu items?), check the selectors by hand"
    stats["note"] = ("no position or email on the list page: they must come from the profile pages (set profile.required: true and add profile fields)"
                     if n and stats["with_position"] == 0 and stats["with_email"] == 0 else None)
    return stats


def learn(html: str, university: str, list_url: str, domains: list[str], client: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """-> (site config dict, validation stats). Raises ValueError when the page shows no repeated person-like list at all."""
    groups = find_repeated_groups(html)
    if not groups:
        raise ValueError("no repeated list structure found on this page (is it JavaScript-rendered? use the rendered DOM)")
    item_css, fields, by = groups[0].selector, None, "heuristic"
    item_is_anchor = groups[0].examples[0].name == "a"
    if client is not None:
        from .llm import SelectorProposal, ask_model
        system = ("You write CSS selectors for a university faculty directory page. Reply with JSON only. item_css must be one of the "
                  "candidate selectors given (the one whose elements are one person each). The other selectors are relative to an item "
                  "and must only use tags/classes visible in the examples; use null when something is not present. "
                  "position_label_regex: a regex with ONE capture group for text like '职称：教授', or null. "
                  "department_previous_css: selector of a heading that precedes groups of items (e.g. 'h4'), or null.")
        try:
            prop = ask_model(client, SelectorProposal, system, skeleton_for_llm(groups), "site_learn", list_url, max_tokens=500)
            chosen = next((g for g in groups if g.selector == prop.item_css), groups[0])
            item_css, item_is_anchor = chosen.selector, chosen.examples[0].name == "a"
            fields = {"name": {"css": prop.name_css or "a"},
                      "profile_href": {"css": prop.link_css or prop.name_css or "a", "attr": "href"}, "emails": {"emails": True}}
            if prop.position_label_regex:
                fields["position"] = {"label": prop.position_label_regex}
            elif prop.position_css:
                fields["position"] = {"css": prop.position_css}
            if prop.department_previous_css:
                fields["department"] = {"previous": prop.department_previous_css}
            if item_is_anchor:
                fields["name"], fields["profile_href"] = {"css": ":scope"}, {"css": ":scope", "attr": "href"}
            by = f"groq:{client.model}"
        except Exception:  # noqa: BLE001 - fall back to the heuristic, say so in the file
            fields, by = None, "heuristic (groq failed)"
    if fields is None:
        fields = heuristic_fields(html, item_css, item_is_anchor)
    tmp = build_site_dict(university, list_url, item_css, fields, [], by, {})
    cfg = SiteConfig(slug="proposed", university=university, list_url=list_url, school_en=None, school_zh=None, render="static",
                     personal_url_patterns=[], list_spec=tmp["list"], profile_spec=tmp["profile"], raw=tmp)
    base = validate_proposal(html, cfg)
    hrefs = [p.profile_href for p in parse_list(html, cfg).people if p.profile_href]
    patterns = url_patterns_from_samples(hrefs)
    out = build_site_dict(university, list_url, item_css, fields, patterns, by, {k: v for k, v in base.items() if k != "sample"})
    return out, base


def to_yaml(data: dict[str, Any]) -> str:
    head = ("# PROPOSED by `extract site-learn`. NOT used until a human has reviewed it:\n"
            "#  1. open the list page and compare the sample below with what you see; fix selectors that are wrong\n"
            "#  2. set school_en / school_zh (page title or site name); check personal_url_patterns (person pages only)\n"
            "#  3. add the university to config/universities.yml and config/domains.yml if it is not there\n"
            "#  4. delete the `needs_review` and `proposal_stats` lines and move this file to config/sites/<name>.yml\n")
    return head + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
