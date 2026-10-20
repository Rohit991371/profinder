from pathlib import Path

from profinder_pipeline.extract.tsinghua_list import INFO_RE, parse_list_html, visible_text

FIX = Path(__file__).parent / "fixtures"
BASE = "https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm"


def _by_name(res):
    return {e.name_raw: e for e in res.entries}


def test_main_layout():
    res = parse_list_html((FIX / "list_sample.html").read_text(encoding="utf-8"), BASE)
    assert res.mode == "h2"
    assert res.raw_entry_count == 8 and res.duplicate_urls == 1 and len(res.entries) == 7
    e = _by_name(res)
    feng = e["冯建华"]
    assert feng.profile_url == "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"
    assert feng.position_zh == "教授" and feng.phones == ["62789150"]
    assert feng.emails == ["fengjh@tsinghua.edu.cn"]
    assert feng.institute_zh == "计算机软件研究所" and feng.rank_group_zh == "正高"


def test_missing_phone_does_not_become_position():
    e = _by_name(parse_list_html((FIX / "list_sample.html").read_text(encoding="utf-8"), BASE))
    assert e["唐杰"].position_zh == "教授" and e["唐杰"].phones == []


def test_mailto_annotation_spaces_and_other_domains():
    e = _by_name(parse_list_html((FIX / "list_sample.html").read_text(encoding="utf-8"), BASE))
    assert e["陈渝"].emails == ["yuchen@tsinghua.edu.cn"]
    assert "段海新（校内兼职）" in e
    assert "尹 霞" in e
    assert e["吴建平"].emails == ["jianping@cernet.edu.cn"]
    assert e["陈宁"].position_zh == "高级工程师"
    assert e["陈宁"].institute_zh == "计算机基础与实验教学部" and e["陈宁"].rank_group_zh == "实验技术人员"


def test_all_profile_urls_are_personal_pages():
    res = parse_list_html((FIX / "list_sample.html").read_text(encoding="utf-8"), BASE)
    assert all(INFO_RE.search(e.profile_url) for e in res.entries)


def test_siblings_layout_without_li():
    res = parse_list_html((FIX / "list_siblings.html").read_text(encoding="utf-8"), BASE)
    e = _by_name(res)
    assert e["张三"].emails == ["zhangsan@tsinghua.edu.cn"] and e["张三"].position_zh == "教授"
    assert e["李四"].emails == ["lisi@tsinghua.edu.cn"] and e["李四"].position_zh == "副教授"


def test_fallback_without_h2():
    res = parse_list_html((FIX / "list_no_h2.html").read_text(encoding="utf-8"), BASE)
    assert res.mode == "fallback_li_links" and len(res.entries) == 1
    assert res.entries[0].emails == ["wangwu@tsinghua.edu.cn"]


def test_visible_text_drops_scripts():
    t = visible_text((FIX / "list_sample.html").read_text(encoding="utf-8"))
    assert "a@b.com" not in t and "冯建华" in t


def test_real_saved_list_if_present():
    path = Path(__file__).resolve().parents[1] / "inputs" / "tsinghua_list.html"
    if not path.exists():
        return  # real file is git-ignored; test only runs on the user's machine
    res = parse_list_html(path.read_text(encoding="utf-8-sig"), BASE)
    assert len(res.entries) >= 100, f"only {len(res.entries)} entries (mode {res.mode})"
    assert all(INFO_RE.search(e.profile_url) for e in res.entries)
