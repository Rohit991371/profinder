"""profile-learn: proposes position / school / email selectors from saved profile pages (synthetic pages shaped like the Central South portal,
which has TWO templates under one host), and site-learn no longer accepts rank tabs as people."""
from profinder_pipeline.extract.profile_learn import css_path, propose_department, propose_position, propose_profile, to_yaml_block, _soup


def page_a(name, rank, unit, extra=""):
    return f"""<html><body><div class="per-infor"><div class="namebx"><div class="nm"><h2>{name}</h2><span>{rank}</span></div>
    <div class="bsbx"><div class="bs bs-1"><p>{rank} 硕士生导师</p><p>所在单位：{unit}</p><p>办公地点：逸夫楼212室</p></div></div></div>
    <div class="pre-pro"><h2>个人简介</h2><p>研究方向为人工智能。{extra}</p></div></div><script>var x=1;</script></body></html>"""


def page_b(name, rank, unit):
    return f"""<html><body><div class="jbqk"><p> {rank}
    博士生导师
    </p><p>电子邮箱：<span style="display:none">811dbd75b77da9f7</span></p></div><div class="main"><h2>{name}</h2></div></body></html>"""


def test_position_selector_covers_both_templates():
    soups = [_soup(page_a(f"甲{i}", "副教授", "计算机学院")) for i in range(5)] + [_soup(page_b(f"乙{i}", "教授", "计算机学院")) for i in range(5)]
    sel, cov = propose_position(soups)
    assert cov == 1.0 and "," in sel                        # one selector per template, joined
    for s in soups:
        assert s.select_one(sel) is not None
    one, cov1 = propose_position(soups[:5])
    assert cov1 == 1.0 and "," not in one


def test_department_label_and_emails():
    texts = [f"x\n所在单位：计算机学院\ny" for _ in range(6)] + ["z"] * 2
    lab, cov = propose_department(texts)
    assert lab == r"所在单位[：:]\s*(\S+)" and cov == 6 / 8
    assert propose_department(["nothing"] * 5) == (None, 0.0)
    prop = propose_profile([page_a("甲", "教授", "计算机学院", "邮箱：abc{at}csu[dot]edu[dot]cn")] * 3 + [page_b("乙", "教授", "x")] * 3)
    assert prop["coverage"]["email_readable"] == 0.5 and prop["coverage"]["position"] == 1.0
    block = to_yaml_block(prop)
    assert block.startswith("profile:\n  required: true") and "position:" in block and "emails:" in block


def test_selectors_need_a_class_and_hidden_script_text_is_ignored():
    s = _soup("<div><p>教授</p></div>")
    assert css_path(s.find("p")) is None                    # no class anywhere: no stable selector
    assert propose_position([s])[0] is None


def test_site_learn_does_not_accept_rank_tabs_as_people():
    from profinder_pipeline.extract.site_learn import _name_ok
    for tab in ("教授", "副教授", "助理教授", "讲师", "研究员", "博士生导师", "全部"):
        assert not _name_ok(tab), tab
    for name in ("安莹", "郭霖", "欧阳娜娜"):
        assert _name_ok(name), name


def test_guess_school_from_the_page_title():
    from profinder_pipeline.extract.site_learn import guess_school
    h = lambda t: f"<html><head><title>{t}</title></head><body></body></html>"   # noqa: E731
    assert guess_school(h("教师名录 - 南京航空航天大学计算机学院")) == "计算机学院"
    assert guess_school(h("师资力量-计算机科学系")) == "计算机科学系"
    assert guess_school(h("Faculty - School of Computer Science and Engineering")) == "School of Computer Science and Engineering"
    assert guess_school(h("首页")) is None
    assert guess_school(h("师资队伍-计算机科学与技术学院、人工智能学院、软件学院")) is None        # several schools: a person decides


def test_promote_moves_a_reviewed_proposal_and_refuses_a_useless_one(tmp_path):
    import yaml
    from profinder_pipeline.extract.profile_learn import find_proposed, promote, write_profile, propose_profile
    pd, sd = tmp_path / "_proposed", tmp_path
    pd.mkdir()
    f = pd / "x_university.yml"
    base = {"needs_review": True, "proposed_by": "heuristic", "proposal_stats": {"people": 20}, "university": "X University", "list_url": "http://x/list",
            "school_en": None, "school_zh": "计算机学院", "personal_url_patterns": [r"^https?://f\.x\.edu\.cn/[^/?#]+/index\.htm$"],
            "list": {"item": "li a", "fields": {"name": {"css": ":scope"}}}, "profile": {"required": False, "fields": {}}}
    f.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    assert find_proposed("X University", pd) == f and find_proposed("Y University", pd) is None
    ok, msg = promote("X University", pd, sd)
    assert not ok and "no position selector" in msg and f.exists()                          # nothing gives a position yet
    write_profile(f, propose_profile([page_a(f"甲{i}", "教授", "计算机学院") for i in range(4)]))
    ok, msg = promote("X University", pd, sd)
    assert ok and not f.exists() and (sd / "x_university.yml").exists()
    out = yaml.safe_load((sd / "x_university.yml").read_text(encoding="utf-8"))
    assert "needs_review" not in out and "proposal_stats" not in out and out["profile"]["fields"]["position"]["css"]
    assert promote("X University", pd, sd)[0] is False                                      # already promoted
    f.write_text(yaml.safe_dump({**base, "personal_url_patterns": [], "school_zh": None}, allow_unicode=True), encoding="utf-8")
    ok, msg = promote("X University", pd, sd)
    assert not ok and "personal_url_patterns is empty" in msg and "school_en / school_zh missing" in msg
