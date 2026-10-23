import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collections import Counter

from profinder_pipeline.extract.css import ProfileData, parse_profile
from profinder_pipeline.extract.emailtools import email_grounded, find_emails_loose
from profinder_pipeline.utils.positions import normalise_position
from profinder_pipeline.validate.areas import RULES, classify_areas
from profinder_pipeline.validate.decide import decide
from profinder_pipeline.validate.grounding import join_interests, keywords_from, lines_grounded, norm_text
from profinder_pipeline.validate.professor_logic import Check, SourcePage, evaluate_person
from profinder_pipeline.schemas.enums import Decision, ProposedStatus, RESEARCH_AREAS
from extract_helpers import ECNU, NOW, SJTU, TSINGHUA, cfg, fake_romanise, html, make_context


def _run(university, fixture, **kw):
    ctx, lp = make_context(university, fixture, **kw)
    out = []
    for raw in lp.people:
        b = evaluate_person(raw, None, None, ctx, romanise_fn=fake_romanise)
        out.append((b, decide(b.role_class, b.position_raw, b.checks)))
    return out


def test_tsinghua_matches_the_spike_counts():
    res = _run(TSINGHUA, "tsinghua_list.html")
    assert Counter(d.value for _, (d, _s, _r) in res) == {"auto_accept": 115, "needs_review": 8, "rejected": 12}
    assert all(b.email for b, _ in res)                                           # cernet/tsinghua domains are allowed
    assert all(b.personal and b.profile_url for b, _ in res)
    verified = [b for b, (d, s, _r) in res if s == ProposedStatus.verified]
    assert all(b.role_class == "faculty" for b in verified)


def test_ecnu_decisions_and_flags():
    res = _run(ECNU, "ecnu_list.html")
    assert Counter(d.value for _, (d, _s, _r) in res) == {"auto_accept": 62, "needs_review": 11, "rejected": 2}
    by = {b.name_zh: (b, d) for b, d in res}
    assert by["胡文心"][1][0] == Decision.rejected                                 # 教授级高级工程师 = engineer
    assert by["沈富可"][1][0] == Decision.rejected                                 # 教授级高工
    assert by["Edwin Sha"][1][0] == Decision.needs_review                          # not a Chinese-script name
    assert by["施江鸣"][1][0] == Decision.needs_review                             # 晨晖学者: role unclear
    assert by["钱鸿"][0].name_zh == "钱鸿" and by["钱鸿"][1][0] == Decision.needs_review   # （兼） concurrent post, spaces removed
    assert by["宋思程"][0].email == "scsong@dase.ecnu.edu.cn"                       # sub-domain of ecnu.edu.cn is allowed


def test_no_record_verified_when_a_blocking_check_fails():
    res = _run(TSINGHUA, "tsinghua_list.html", status=404)
    assert all(d != Decision.auto_accept for _, (d, _s, _r) in res)
    assert all(d == Decision.rejected for _, (d, _s, _r) in res)                   # B1: page not HTTP 200


def test_list_count_flags_send_everything_to_review():
    flag = Check("S_list_count_drift", False, False, "135 -> 60 people")
    res = _run(TSINGHUA, "tsinghua_list.html", university_flags=[flag])
    assert Counter(d.value for _, (d, _s, _r) in res).get("auto_accept", 0) == 0


def test_email_domain_rule_and_suppress_list():
    ctx, lp = make_context(TSINGHUA, "tsinghua_list.html", suppress_emails={"fengjh@tsinghua.edu.cn"})
    ctx.email_domains = ["tsinghua.edu.cn"]                                        # without the cernet exception
    first = evaluate_person(lp.people[0], None, None, ctx, romanise_fn=fake_romanise)
    assert first.email is None and any(c.name == "F_email_suppressed" for c in first.checks)
    cernet = [p for p in lp.people if any(e.endswith("@cernet.edu.cn") for e in p.emails)]
    assert cernet and all(evaluate_person(p, None, None, ctx, romanise_fn=fake_romanise).email is None for p in cernet)


def test_email_must_be_on_the_page():
    ctx, lp = make_context(TSINGHUA, "tsinghua_list.html")
    raw = lp.people[0]
    raw.emails = ["not.on.page@tsinghua.edu.cn"]
    b = evaluate_person(raw, None, None, ctx, romanise_fn=fake_romanise)
    assert b.email is None and any(c.name == "F_email_grounded" and not c.passed for c in b.checks)


def test_profile_required_site_uses_profile_page_as_source():
    ctx, lp = make_context(SJTU, "sjtu_list_rendered.html")
    c = cfg(SJTU)
    raw = next(p for p in lp.people if p.profile_href.endswith("/chenrong.html"))
    h = html("sjtu_profile_chen_rong.html")
    prof = parse_profile(h, c, raw.profile_href)
    from profinder_pipeline.extract.css import visible_text
    pp = SourcePage(raw.profile_href, 200, NOW, visible_text(h))
    b = evaluate_person(raw, prof, pp, ctx, romanise_fn=fake_romanise)
    assert b.source_url == raw.profile_href and b.position_en == "Professor" and b.email == "rongchen@sjtu.edu.cn"
    assert b.personal_website == "https://ipads.se.sjtu.edu.cn/rong_chen" and b.interests is None
    assert decide(b.role_class, b.position_raw, b.checks)[0] == Decision.auto_accept
    # a failed profile fetch makes B1 fail -> rejected, never verified
    pp404 = SourcePage(raw.profile_href, 404, NOW, "")
    b2 = evaluate_person(raw, prof, pp404, ctx, romanise_fn=fake_romanise)
    assert decide(b2.role_class, b2.position_raw, b2.checks)[0] == Decision.rejected


def test_interests_grounding_keywords_and_areas():
    ctx, lp = make_context(TSINGHUA, "tsinghua_list.html")
    c = cfg(TSINGHUA)
    h = html("tsinghua_profile_feng_jianhua.html")
    from profinder_pipeline.extract.css import visible_text
    prof = parse_profile(h, c, "https://x/")
    pp = SourcePage("https://x/", 200, NOW, visible_text(h))
    b = evaluate_person(lp.people[0], prof, pp, ctx, romanise_fn=fake_romanise)
    assert b.interests == "数据库管理系统，数据安全与隐私保护，信息检索"
    assert b.keywords == "数据库管理系统, 数据安全与隐私保护, 信息检索"
    assert b.areas == ["Databases", "Privacy"]
    assert b.personal_website == "http://dbgroup.cs.tsinghua.edu.cn/"
    # interest text that is not on the page is dropped
    prof.interests_lines = ["invented interests"]
    b2 = evaluate_person(lp.people[0], prof, pp, ctx, romanise_fn=fake_romanise)
    assert b2.interests is None and any(c.name == "F_interests_grounded" and not c.passed for c in b2.checks)


def test_areas_only_from_text_and_only_the_24_names():
    assert set(RULES) | {"Blockchain Security"} == set(RESEARCH_AREAS)
    assert classify_areas("") == [] and classify_areas("计算机学院 软件研究所") == []      # no department-name tagging
    assert [h.area for h in classify_areas("区块链安全与智能合约")] == ["Blockchain Security"]
    assert classify_areas("区块链技术") == []
    assert [h.area for h in classify_areas("操作系统、分布式系统")] == ["Distributed Systems", "Operating Systems"]
    assert classify_areas("机器学习 深度学习 自然语言处理 计算机视觉 机器人 密码学 隐私 数据库", ) and len(classify_areas("机器学习 深度学习 自然语言处理 计算机视觉 机器人 密码学 隐私 数据库")) == 5


def test_grounding_helpers():
    assert norm_text("陈\u3000榕 ") == "陈榕"
    assert lines_grounded(["数据库，信息检索"], norm_text("研究领域\n数据库， 信息检索\n"))
    assert not lines_grounded(["不存在"], norm_text("abc"))
    assert join_interests(["a", "b"]) == "a；b" and keywords_from("a，bb；ccc") == "bb, ccc"
    assert len((keywords_from("，".join(f"关键词{i}" for i in range(30))) or "").split(", ")) == 10


def test_email_loose():
    assert find_emails_loose("Email：mlan @ cs.ecnu.edu.cn$") == ["mlan@cs.ecnu.edu.cn"]
    assert email_grounded("mlan@cs.ecnu.edu.cn", "x\nEmail： mlan @ cs.ecnu.edu.cn\n")
    assert not email_grounded("a@b.cn", "nothing here")


def test_positions_decisions_from_iteration_4():
    assert normalise_position("助理研究员") == ("Assistant Researcher", "faculty")       # decided: faculty
    assert normalise_position("教授级高工")[1] == "non_faculty" and normalise_position("教授级高级工程师")[1] == "non_faculty"
    assert normalise_position("长聘教授") == ("Professor", "faculty")
    assert normalise_position("Visiting Professor")[1] == "unknown"                       # adjunct/visiting -> review


def test_decide_rules():
    ok = [Check("B1_official_page_200", True), Check("B3_faculty_role", True)]
    assert decide("faculty", "教授", ok)[:2] == (Decision.auto_accept, ProposedStatus.verified)
    assert decide("non_faculty", "工程师", ok)[0] == Decision.rejected
    assert decide("unknown", "x", ok)[0] == Decision.needs_review
    assert decide("faculty", "x", ok + [Check("B2_name_in_page_text", False)])[0] == Decision.needs_review
    assert decide("faculty", "x", ok + [Check("S_unusual_name", False, False)])[0] == Decision.needs_review
    assert decide("faculty", "x", ok + [Check("F_email_domain", False, False)])[0] == Decision.auto_accept
