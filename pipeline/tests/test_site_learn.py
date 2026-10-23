import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml

from profinder_pipeline.extract.site_learn import (find_repeated_groups, heuristic_fields, learn, skeleton_for_llm, to_yaml,
                                                   url_patterns_from_samples, validate_proposal)
from profinder_pipeline.extract.sitecfg import SITES_DIR, SiteConfig, load_site_config, load_site_configs
from extract_helpers import html


def test_groups_found_on_the_three_real_lists():
    t = find_repeated_groups(html("tsinghua_list.html"))
    assert t and t[0].count >= 135 and "li" in t[0].selector
    e = find_repeated_groups(html("ecnu_list.html"))
    assert e[0].selector == "div.team li" and e[0].count >= 75
    s = find_repeated_groups(html("sjtu_list_rendered.html"))
    assert s[0].examples[0].name == "a"                                            # the item is the person link itself
    assert "CANDIDATE 1" in skeleton_for_llm(t) and len(skeleton_for_llm(t)) < 6000


def test_heuristic_proposals_validate_by_running_them():
    for fx, minimum in (("tsinghua_list.html", 130), ("ecnu_list.html", 70), ("sjtu_list_rendered.html", 200)):
        data, stats = learn(html(fx), "Some University", "https://www.example.edu.cn/list.htm", [])
        assert data["needs_review"] is True and data["proposed_by"] == "heuristic" and data["school_en"] is None
        assert stats["people"] >= minimum and stats["names_ok"] >= 0.8 and stats["verdict"] == "plausible"
    ecnu, st = learn(html("ecnu_list.html"), "X", "https://cs.ecnu.edu.cn/jzgml/list.htm", [])
    assert "position" in ecnu["list"]["fields"] and st["with_position"] > 0.9 and st["with_email"] > 0.9


def test_bad_selectors_are_reported_not_trusted():
    cfg = SiteConfig(slug="x", university="X", list_url="https://x.edu.cn/l", school_en=None, school_zh=None, render="static", personal_url_patterns=[],
                     list_spec={"item": "div.team li", "fields": {"name": {"css": "span.nothing"}, "profile_href": {"css": "a", "attr": "href"}}},
                     profile_spec={"required": False, "fields": {}})
    assert validate_proposal(html("ecnu_list.html"), cfg)["verdict"].startswith("poor")
    try:
        learn("<html><body><p>nothing here</p></body></html>", "X", "https://x.edu.cn/l", [])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_url_patterns_generalise():
    assert url_patterns_from_samples([f"https://a.edu.cn/info/1111/{n}.htm" for n in range(3490, 3500)]) == [r"https://a\.edu\.cn/info/\d+/\d+\.htm$"]
    assert url_patterns_from_samples([f"https://a.edu.cn/t/{c}.html" for c in "abcdefgh"]) == [r"https://a\.edu\.cn/t/[^/]+$"]


def test_proposed_file_is_inert_until_reviewed(tmp_path):
    data, _ = learn(html("ecnu_list.html"), "Proposed University", "https://p.edu.cn/list.htm", [])
    text = to_yaml(data)
    assert text.startswith("# PROPOSED") and "needs_review: true" in text
    f = tmp_path / "proposed.yml"
    f.write_text(text, encoding="utf-8")
    assert load_site_config(f).needs_review is True
    assert "Proposed University" not in load_site_configs(tmp_path)                 # skipped until a human removes the flag
    assert "Proposed University" in load_site_configs(tmp_path, include_unreviewed=True)
    reviewed = yaml.safe_load(text)
    reviewed.pop("needs_review")
    (tmp_path / "ok.yml").write_text(yaml.safe_dump(reviewed, allow_unicode=True), encoding="utf-8")
    assert "Proposed University" in load_site_configs(tmp_path)
    assert (SITES_DIR / "_proposed").name == "_proposed"                             # proposals live in a folder the loader never scans


def test_site_learn_with_groq_uses_a_listed_selector_and_falls_back_on_failure(tmp_path):
    import json
    from profinder_pipeline.extract.llm_core import GroqClient, TokenLimiter
    prop = {"item_css": "div.team li", "name_css": ".li-text p a", "link_css": ".li-text p a", "position_css": None,
            "position_label_regex": "职称[：:]\\s*([^\\n$]*)", "department_previous_css": None}

    def make(replies):
        t = [0.0]
        def transport(payload, headers, timeout):
            r = replies.pop(0)
            return (200, {"choices": [{"message": {"content": r}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, {}) if r is not None else (500, {}, {})
        return GroqClient(api_key="k", model="m", transport=transport, limiter=TokenLimiter(clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)),
                          sleep=lambda s: None, max_attempts=1)
    data, stats = learn(html("ecnu_list.html"), "X", "https://cs.ecnu.edu.cn/jzgml/list.htm", [], make([json.dumps(prop)]))
    assert data["proposed_by"] == "groq:m" and data["list"]["item"] == "div.team li" and stats["people"] >= 70
    prop["item_css"] = "div.invented li"                                             # not one of the candidates -> use the best candidate
    data2, _ = learn(html("ecnu_list.html"), "X", "https://cs.ecnu.edu.cn/jzgml/list.htm", [], make([json.dumps(prop)]))
    assert data2["list"]["item"] == "div.team li"
    data3, _ = learn(html("ecnu_list.html"), "X", "https://cs.ecnu.edu.cn/jzgml/list.htm", [], make([None]))
    assert data3["proposed_by"].startswith("heuristic")


NAV_AND_PEOPLE = """<html><body>
<div class="top"><ul class="sub">%s</ul></div>
<div class="list"><ul>%s</ul></div></body></html>""" % (
    "".join(f'<li><a href="/About/{w.replace(" ", "_")}.htm">{w}</a></li>' for w in
            ["College Introduction", "Undergraduate program", "Postgraduate", "Open Class", "All Teachers", "News", "Contact Us", "Research",
             "Admissions", "Alumni", "Library", "Links"]),
    "".join(f'<li><a href="/p/{i}.htm">{n}</a><span>Professor</span></li>' for i, n in enumerate(
        ["Qin Jin", "Ye Xiang", "Cao Wei", "You Xiaodong", "Zhan Jiang", "Li Ming", "Wang Hao", "Zhao Lei", "Sun Yan", "Gao Feng"])))


def test_menus_are_not_proposed_as_people():
    data, stats = learn(NAV_AND_PEOPLE, "X", "https://x.edu.cn/l", [])
    assert data["list"]["item"] == "div.list li" and stats["people"] == 10 and stats["names_ok"] == 1.0 and stats["verdict"] == "plausible"
    nav_only = "<html><body><ul class='sub'>%s</ul></body></html>" % "".join(
        f'<li><a href="/a/{i}.htm">{w}</a></li>' for i, w in enumerate(
            ["College Introduction", "Undergraduate program", "Postgraduate", "Open Class", "All Teachers", "News", "Contact Us", "Research", "Admissions"]))
    _d, st2 = learn(nav_only, "X", "https://x.edu.cn/l", [])
    assert st2["verdict"].startswith("poor") and st2["names_ok"] < 0.5


def test_names_only_lists_get_a_note():
    _d, st = learn(NAV_AND_PEOPLE.replace("<span>Professor</span>", ""), "X", "https://x.edu.cn/l", [])
    assert st["with_position"] == 0 and "profile pages" in st["note"]
