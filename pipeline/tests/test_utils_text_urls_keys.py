import pytest

from profinder_pipeline.utils.keys import md5_hex, professor_key, program_key, scholarship_key, university_key, url_hash
from profinder_pipeline.utils.text import clean_name, is_cjk_only, normalise_key_text, split_zh_name
from profinder_pipeline.utils.urls import canonical_url, host_in_domains, host_of, is_http_url


def test_canonical_url():
    assert canonical_url("https://WWW.cs.tsinghua.edu.cn/info/1111/3490.htm#top") == "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"
    assert canonical_url("https://a.edu.cn/x/index.htm?utm_source=z&id=3") == "https://a.edu.cn/x?id=3"
    assert canonical_url("https://a.edu.cn/x/") == "https://a.edu.cn/x"
    assert canonical_url("https://a.edu.cn") == "https://a.edu.cn/"
    assert canonical_url("https://a.edu.cn/index.html") == "https://a.edu.cn/"
    assert canonical_url("https://a.edu.cn/p?spm=1&fbclid=2&k=v") == "https://a.edu.cn/p?k=v"


def test_host_helpers():
    assert host_of("https://CS.Tsinghua.edu.cn/x") == "cs.tsinghua.edu.cn"
    assert host_in_domains("https://cs.tsinghua.edu.cn/x", ["tsinghua.edu.cn"])
    assert host_in_domains("tsinghua.edu.cn", ["tsinghua.edu.cn"])
    assert not host_in_domains("https://evil-tsinghua.edu.cn/", ["tsinghua.edu.cn"])
    assert not host_in_domains("https://tsinghua.edu.cn.evil.com/", ["tsinghua.edu.cn"])
    assert is_http_url("https://x.y") and not is_http_url("javascript:void(0)")


def test_url_hash_is_md5_of_canonical_url():
    assert url_hash("https://A.edu.cn/x/#f") == md5_hex("https://a.edu.cn/x")
    assert len(url_hash("https://a.edu.cn/")) == 32


def test_clean_name_and_split():
    assert clean_name("段海新（校内兼职）") == ("段海新", "校内兼职")
    assert clean_name("尹 霞") == ("尹霞", None)
    assert clean_name("John  Smith") == ("John Smith", None)
    assert split_zh_name("冯建华") == ("冯", "建华")
    assert split_zh_name("欧阳锋") == ("欧阳", "锋")
    assert split_zh_name("张") == ("张", "")
    assert split_zh_name("John Smith") is None


def test_cjk_and_key_text_keep_chinese():
    assert is_cjk_only("教授") and not is_cjk_only("Professor") and not is_cjk_only("教授 Professor") and not is_cjk_only("")
    assert normalise_key_text("Computer Science & Technology!") == "computerscience" + "technology"
    assert normalise_key_text("计算机 科学") == "计算机科学"      # the app's normalizeName would return "" here
    assert normalise_key_text(None) == ""


def test_university_key():
    assert university_key("10003", "Tsinghua University") == md5_hex("10003")
    assert university_key(None, "Tsinghua University") == md5_hex("tsinghuauniversity")
    assert university_key("  ", "Tsinghua University") == md5_hex("tsinghuauniversity")


def test_professor_key_personal_vs_not():
    k1 = professor_key("u1", "https://a.edu.cn/info/1/2.htm#x", True, "冯建华", "Feng Jianhua", "CS")
    k2 = professor_key("u1", "https://A.edu.cn/info/1/2.htm", True, "other", None, None)
    assert k1 == k2 == md5_hex("u1|https://a.edu.cn/info/1/2.htm")
    k3 = professor_key("u1", "https://a.edu.cn/dir.htm", False, "冯建华", "Feng Jianhua", "Dept. of CS")
    assert k3 == md5_hex("u1|冯建华|deptofcs")
    assert professor_key("u1", None, False, None, "Feng Jianhua", None) == md5_hex("u1|fengjianhua|")
    with pytest.raises(ValueError):
        professor_key("u1", None, False, None, None, None)


def test_program_and_scholarship_keys_normalise():
    assert program_key("u1", "PhD", "Computer Science") == program_key("u1", "phd", " computer  science ")
    assert program_key("u1", "PhD", "Computer Science") != program_key("u1", "Master", "Computer Science")
    assert scholarship_key("u1", "CSC Scholarship") == scholarship_key("u1", "csc  scholarship")
