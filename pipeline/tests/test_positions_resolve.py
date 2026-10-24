"""Iteration 5 position rule (list vs profile). Pure Python."""
from profinder_pipeline.utils.positions import normalise_primary, resolve_position


def test_primary_skips_honours_and_takes_first_known_role():
    assert normalise_primary("青年研究员；上海人工智能实验室副研究员；博导") == ("Researcher", "faculty")
    assert normalise_primary("博士生导师") == (None, "unknown")
    assert normalise_primary("国家杰青，多媒体与计算机视觉实验室主任") == (None, "unknown")
    assert normalise_primary("博士生导师；教授") == ("Professor", "faculty")
    assert normalise_primary(None) == (None, "unknown") and normalise_primary("  ") == (None, "unknown")
    assert normalise_primary("Professor, Department of X") == ("Professor", "faculty")


def test_profile_honour_does_not_override_list_rank():          # the ECNU cases from the Entry 13 output
    for list_raw, profile_raw, en in [("副教授", "上海市领军人才", "Associate Professor"), ("教授", "博士生导师", "Professor"),
                                      ("副研究员", "科研教学", "Associate Researcher"), ("教授", "信息学部主任", "Professor"),
                                      ("副教授", "教师", "Associate Professor"), ("讲师", "教师", "Lecturer"),
                                      ("副教授", "博士", "Associate Professor"), ("教授", "国家杰青，多媒体与计算机视觉实验室主任", "Professor")]:
        r = resolve_position(list_raw, profile_raw)
        assert (r.en, r.role, r.conflict) == (en, "faculty", None), (list_raw, profile_raw, r)


def test_title_list_in_profile_is_not_a_conflict():
    r = resolve_position("青年研究员", "青年研究员；上海人工智能实验室副研究员；博导")
    assert r.role == "faculty" and r.en == "Researcher" and r.conflict is None


def test_two_different_faculty_ranks_stay_a_soft_conflict_and_profile_wins():
    r = resolve_position("副教授", "副研究员")
    assert r.role == "faculty" and r.en == "Associate Researcher" and r.raw == "副研究员" and "副教授" in r.conflict


def test_non_faculty_on_either_side_wins():
    r = resolve_position("教授级高工", "信息化治理委员会秘书长")
    assert r.role == "non_faculty" and r.raw == "教授级高工"
    r = resolve_position("教授", "高级工程师")
    assert r.role == "non_faculty" and r.raw == "高级工程师"


def test_no_information_is_unknown_and_profile_only_works():
    assert resolve_position(None, None).role == "unknown"
    assert resolve_position(None, "教师").role == "unknown"
    r = resolve_position(None, "教授")
    assert (r.en, r.role) == ("Professor", "faculty")
    assert resolve_position("教授", None).en == "Professor"
