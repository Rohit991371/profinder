"""`extract profile-learn` (Iteration 6): propose the `profile:` block of a site config from a few CACHED profile pages, with coverage numbers.

Why: the list page of most sites has only names and links; position, school and email sit on each person's page, and writing those selectors by
hand (looking at HTML) was the slowest step per university. This command looks at N saved profile pages (no network) and finds
  position   - the smallest element whose short text contains a rank word (教授, 副教授, 研究员, 讲师, Professor ...), as a CSS selector;
               several templates on one site give a comma-separated selector list
  department - a label such as 所在单位： / 学院： followed by the value (a `label` spec)
  emails     - whether a readable email occurs in the page text at all (hidden/encrypted fields are not visible and are not decoded)
Every proposal is checked by running it on the sample pages; the coverage is printed. A person still reviews the result: it is a starting point.
Pure functions (propose_profile) + a thin CLI."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

from .emailtools import find_emails_loose

RANK = re.compile(r"(教授|研究员|讲师|副研|助理研究员|工程师|Professor|Lecturer|Researcher|Assistant Professor|Associate Professor)", re.I)
LABELS = ("所在单位", "所在学院", "工作单位", "所属单位", "所属学院", "院系", "学院", "部门", "单位")
MAX_RANK_TEXT = 40
_CLS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _soup(html: str) -> BeautifulSoup:
    s = BeautifulSoup(html or "", "html.parser")
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    return s


def _seg(el: Tag) -> str:
    cls = [c for c in (el.get("class") or []) if _CLS.match(c) and not re.search(r"\d", c)]
    return el.name + ("." + cls[0] if cls else "")


def css_path(el: Tag) -> Optional[str]:
    """tag.class chain from the element up to (and including) the nearest two ancestors that carry a class; None if there is no class at all."""
    segs, classed, cur = [_seg(el)], 1 if el.get("class") else 0, el.parent
    while isinstance(cur, Tag) and cur.name not in ("body", "html", "[document]") and classed < 3:
        s = _seg(cur)
        if "." in s:
            segs.append(s)
            classed += 1
        cur = cur.parent
    path = " ".join(reversed(segs))
    return path if "." in path else None


def _rank_elements(soup: BeautifulSoup) -> list:
    out = []
    for el in soup.find_all(True):
        if el.name in ("html", "body", "head", "title", "a"):
            continue
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if txt and len(txt) <= MAX_RANK_TEXT and RANK.search(txt) and not any(
                isinstance(c, Tag) and RANK.search(c.get_text(" ", strip=True) or "") and len(c.get_text(" ", strip=True)) <= MAX_RANK_TEXT for c in el.find_all(True)):
            out.append(el)                         # the SMALLEST element: none of its children is itself a rank text
    return out


def _hit(soup: BeautifulSoup, selector: str) -> bool:
    try:
        el = soup.select_one(selector)
    except Exception:  # noqa: BLE001 - a malformed selector never matches
        return False
    if el is None:
        return False
    t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    return bool(t) and len(t) <= MAX_RANK_TEXT and bool(RANK.search(t))


def propose_position(soups: list) -> tuple:
    """-> (selector or None, coverage 0-1). Greedy: the selector that works on most pages, then one that covers the pages still missing (max 3)."""
    n = len(soups) or 1
    counts: Counter = Counter()
    for s in soups:
        seen = {css_path(e) for e in _rank_elements(s)}
        counts.update(p for p in seen if p)
    chosen: list[str] = []
    covered: set[int] = set()
    for _ in range(3):
        best, best_gain = None, 0
        for sel, _c in counts.most_common(25):
            if sel in chosen:
                continue
            gain = sum(1 for i, s in enumerate(soups) if i not in covered and _hit(s, sel))
            if gain > best_gain:
                best, best_gain = sel, gain
        if not best:
            break
        chosen.append(best)
        union = ", ".join(chosen)
        covered = {i for i, s in enumerate(soups) if _hit(s, union)}
        if len(covered) / n >= 0.95:
            break
    if not chosen:
        return None, 0.0
    union = ", ".join(chosen)
    return union, sum(1 for s in soups if _hit(s, union)) / n


def propose_department(texts: list) -> tuple:
    """-> (label regex or None, coverage). The label that is followed by a value on most pages."""
    best, best_cov = None, 0.0
    for lab in LABELS:
        rx = re.compile(re.escape(lab) + r"[：:]\s*(\S+)")
        cov = sum(1 for t in texts if rx.search(t)) / (len(texts) or 1)
        if cov > best_cov + 1e-9:
            best, best_cov = lab, cov
    return (best + r"[：:]\s*(\S+)" if best and best_cov >= 0.3 else None), best_cov


def propose_profile(htmls: list) -> dict[str, Any]:
    soups = [_soup(h) for h in htmls]
    texts = ["\n".join(s.get_text("\n", strip=True).splitlines()) for s in soups]
    pos, pos_cov = propose_position(soups)
    dep, dep_cov = propose_department(texts)
    mail_cov = sum(1 for t in texts if find_emails_loose(t)) / (len(texts) or 1)
    fields: dict[str, Any] = {}
    if pos:
        fields["position"] = {"css": pos}
    if dep:
        fields["department"] = {"label": dep}
    fields["emails"] = {"emails": True}
    return {"profile": {"required": True, "fields": fields},
            "coverage": {"pages": len(htmls), "position": round(pos_cov, 2), "department": round(dep_cov, 2), "email_readable": round(mail_cov, 2)}}


def to_yaml_block(prop: dict[str, Any]) -> str:
    f = prop["profile"]["fields"]
    lines = ["profile:", "  required: true", "  fields:"]
    if "position" in f:
        lines.append(f"    position:   {{css: \"{f['position']['css']}\"}}")
    if "department" in f:
        lines.append(f"    department: {{label: '{f['department']['label']}'}}")
    lines.append("    emails:     {emails: true}")
    return "\n".join(lines)


def cmd_profile_learn(args) -> int:
    from ..paths import INPUTS_DIR
    from ..fetch.cache import PageCache
    from .run import _db_connect
    cache = PageCache(INPUTS_DIR / "cache" / "pages")
    urls: list[str] = list(args.url or [])
    if not urls:
        with _db_connect() as conn:
            rows = conn.execute("SELECT url FROM ingest.target WHERE university_name = %s AND kind = 'faculty_profile' ORDER BY url", (args.university,)).fetchall()
        urls = [r[0] for r in rows]
    htmls, used = [], []
    step = max(1, len(urls) // max(1, args.n * 2)) if urls else 1
    for u in urls[::step]:
        p = cache.read(u, allow_stale=True)
        if p and p.status == 200 and len(p.html) > 500:
            htmls.append(p.html)
            used.append(u)
        if len(htmls) >= args.n:
            break
    if len(htmls) < 2:
        print(f"only {len(htmls)} cached profile pages for {args.university}: run `fetch run --only \"{args.university}\" --budget {args.n * 2}` first "
              f"(if most pages fail, see `fetch run ... --delay 6` and the ingest.page error query in the guide)")
        return 1
    if len(htmls) < 5:
        print(f"WARNING: only {len(htmls)} cached profile pages: the coverage numbers are rough")
    prop = propose_profile(htmls)
    c = prop["coverage"]
    print(f"{args.university}: looked at {c['pages']} cached profile pages")
    print(f"coverage: position {c['position']:.0%} | department label {c['department']:.0%} | readable email {c['email_readable']:.0%}")
    print("\n# paste into config/sites/<slug>.yml (replace any existing `profile:` block), then check with extract run --no-db / extract show\n")
    print(to_yaml_block(prop))
    if c["position"] < 0.8:
        print("\nNOTE: position coverage is low: look at a page with `fetch cached <url> --around \"<rank word>\"` and write the selector by hand.")
    if c["email_readable"] < 0.3:
        print("NOTE: few readable emails: they may be in hidden/script fields (not decoded) or not published; empty emails are acceptable.")
    print("\nsample pages used:\n  " + "\n  ".join(used[:5]))
    if getattr(args, "write", False):
        path = find_proposed(args.university)
        if path is None:
            print("\n--write: no proposal for this university in config/sites/_proposed/ (run `batch run --only NAME --learn` first)")
            return 1
        write_profile(path, prop)
        print(f"\nwritten into {path}: now run `extract promote --university \"{args.university}\"`")
    return 0


# ------------------------------------------------------------------------------------------------ proposal file helpers
def _dirs():
    from ..paths import CONFIG_DIR
    return CONFIG_DIR / "sites" / "_proposed", CONFIG_DIR / "sites"


def find_proposed(university: str, proposed_dir=None):
    import yaml
    d = proposed_dir or _dirs()[0]
    for f in sorted(d.glob("*.yml")) if d.is_dir() else []:
        try:
            if (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("university") == university:
                return f
        except Exception:  # noqa: BLE001 - an unreadable proposal is skipped
            continue
    return None


def write_profile(path, prop: dict) -> None:
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["profile"] = prop["profile"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def promote(university: str, proposed_dir=None, sites_dir=None) -> tuple:
    """Check a proposal and move it to config/sites/. -> (ok, message). Refuses what would produce a useless config."""
    import yaml
    pd, sd = _dirs()
    pd, sd = proposed_dir or pd, sites_dir or sd
    f = find_proposed(university, pd)
    if f is None:
        return False, f"no proposal for {university!r} in {pd} (already promoted? check config/sites/)"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    problems = []
    if not data.get("personal_url_patterns"):
        problems.append("personal_url_patterns is empty: the person links are not recognised")
    if not (data.get("school_en") or data.get("school_zh")):
        problems.append("school_en / school_zh missing: set one (the school whose faculty list this is)")
    list_fields = ((data.get("list") or {}).get("fields") or {})
    prof_fields = ((data.get("profile") or {}).get("fields") or {})
    if "position" not in list_fields and "position" not in prof_fields:
        problems.append("no position selector anywhere: run `extract profile-learn --university ... --write` (or add a profile.position selector)")
    if problems:
        return False, "NOT promoted: " + "; ".join(problems)
    for k in ("needs_review", "proposal_stats", "proposed_by"):
        data.pop(k, None)
    target = sd / f.name
    sd.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    f.unlink()
    return True, f"promoted: {target}"


def cmd_promote(args) -> int:
    ok, msg = promote(args.university)
    print(msg)
    if ok:
        print(f"next: python -m profinder_pipeline extract run --only \"{args.university}\" --no-db   then   extract show --only \"{args.university}\"")
    return 0 if ok else 1
