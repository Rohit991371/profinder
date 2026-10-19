from spike.textutils import (canonical_url, clean_name, email_domain_ok, find_emails, normalise_position, split_zh_name)


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


def test_canonical_url():
    assert canonical_url("https://WWW.cs.tsinghua.edu.cn/info/1111/3490.htm#top") == "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"
    assert canonical_url("https://a.edu.cn/x/index.htm?utm_source=z&id=3") == "https://a.edu.cn/x?id=3"
    assert canonical_url("https://a.edu.cn/x/") == "https://a.edu.cn/x"


def test_position_normaliser():
    assert normalise_position("教授") == ("Professor", "faculty")
    assert normalise_position("副教授") == ("Associate Professor", "faculty")
    assert normalise_position("助理研究员") == ("Assistant Researcher", "faculty")
    assert normalise_position("高级工程师") == ("Engineer/Technician", "non_faculty")
    assert normalise_position("正高级工程师")[1] == "non_faculty"
    assert normalise_position("博士后")[1] == "non_faculty"
    assert normalise_position("特聘教授") == ("Professor", "faculty")
    assert normalise_position("") == (None, "unknown")
    assert normalise_position("书记") == (None, "unknown")


def test_clean_name():
    assert clean_name("段海新（校内兼职）") == ("段海新", "校内兼职")
    assert clean_name("尹 霞") == ("尹霞", None)
    assert clean_name("John  Smith") == ("John Smith", None)


def test_split_zh_name():
    assert split_zh_name("冯建华") == ("冯", "建华")
    assert split_zh_name("欧阳锋") == ("欧阳", "锋")
    assert split_zh_name("张") == ("张", "")
    assert split_zh_name("John Smith") is None
