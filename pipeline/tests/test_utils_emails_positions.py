from profinder_pipeline.utils.emails import email_domain_ok, email_on_page, find_emails, is_valid_email
from profinder_pipeline.utils.positions import normalise_position


def test_find_emails_plain_and_obfuscated():
    assert find_emails("Mail: FengJH@Tsinghua.edu.cn, call 123") == ["fengjh@tsinghua.edu.cn"]
    assert find_emails("fengjh [at] tsinghua.edu.cn") == ["fengjh@tsinghua.edu.cn"]
    assert find_emails("fengjh (at) tsinghua (dot) edu (dot) cn") == ["fengjh@tsinghua.edu.cn"]
    assert find_emails("fengjh AT tsinghua.edu.cn") == ["fengjh@tsinghua.edu.cn"]
    assert find_emails("no email here, meet at noon") == []


def test_email_domain_rule():
    allowed = ["tsinghua.edu.cn"]
    assert email_domain_ok("a@tsinghua.edu.cn", allowed)
    assert email_domain_ok("a@mail.tsinghua.edu.cn", allowed)
    assert not email_domain_ok("a@cernet.edu.cn", allowed)
    assert not email_domain_ok("a@evil-tsinghua.edu.cn", allowed)
    assert not email_domain_ok("a@tsinghua.edu.cn.evil.com", allowed)
    assert email_domain_ok("a@cernet.edu.cn", allowed + ["cernet.edu.cn"])


def test_email_regex_and_grounding():
    assert is_valid_email("a.b+c@x.edu.cn") and not is_valid_email("a@b") and not is_valid_email("") and not is_valid_email(None)
    assert email_on_page("Fengjh@tsinghua.edu.cn", "contact: fengjh [at] tsinghua [dot] edu [dot] cn")
    assert not email_on_page("other@tsinghua.edu.cn", "contact: fengjh@tsinghua.edu.cn")


def test_position_chinese():
    assert normalise_position("教授") == ("Professor", "faculty")
    assert normalise_position("副教授") == ("Associate Professor", "faculty")
    assert normalise_position("助理研究员") == ("Assistant Researcher", "faculty")
    assert normalise_position("高级工程师") == ("Engineer/Technician", "non_faculty")
    assert normalise_position("正高级工程师")[1] == "non_faculty"
    assert normalise_position("博士后")[1] == "non_faculty"
    assert normalise_position("特聘教授") == ("Professor", "faculty")
    assert normalise_position("教授级高级工程师")[1] == "non_faculty"
    assert normalise_position("") == (None, "unknown")
    assert normalise_position(None) == (None, "unknown")
    assert normalise_position("书记") == (None, "unknown")


def test_position_english():
    assert normalise_position("Professor") == ("Professor", "faculty")
    assert normalise_position("Associate Professor") == ("Associate Professor", "faculty")
    assert normalise_position("assistant  professor") == ("Assistant Professor", "faculty")
    assert normalise_position("Senior Lecturer") == ("Lecturer", "faculty")
    assert normalise_position("Postdoctoral Fellow") == ("Postdoctoral Researcher", "non_faculty")
    assert normalise_position("PhD Student")[1] == "non_faculty"
    assert normalise_position("Laboratory Engineer")[1] == "non_faculty"
    assert normalise_position("Visiting Professor") == (None, "unknown")     # a human decides
    assert normalise_position("Emeritus Professor") == (None, "unknown")
    assert normalise_position("Dean") == (None, "unknown")
