import pytest

pytest.importorskip("pypinyin")


def test_romanise_family_given():
    from profinder_pipeline.utils.names import romanise
    assert romanise("冯建华") == ("Feng Jianhua", "Jianhua", "Feng")
    assert romanise("唐杰") == ("Tang Jie", "Jie", "Tang")
    assert romanise("单霁翔")[2] == "Shan"
    assert romanise("欧阳锋")[2].lower().startswith("ouyang")
    assert romanise("张") == ("Zhang", None, "Zhang")
    assert romanise("John Smith") is None
