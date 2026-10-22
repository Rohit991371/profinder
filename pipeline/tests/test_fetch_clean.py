import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fetch_helpers import fixture
from profinder_pipeline.fetch.clean import html_to_markdown, truncate_for_llm

PAGE = """<html><head><title>教职工名录 - 计算机系</title><style>.a{}</style><script>var x=1;</script></head><body>
<div class="g-nav"><a href="/">首页</a><a href="/news">新闻</a></div>
<div class="breadcrumb">当前位置：首页 &gt; 师资</div>
<h2>教授</h2>
<ul><li><a href="/info/1/2.htm">蒋　力</a> 教授</li><li><a href="mailto:li@x.edu.cn">li@x.edu.cn</a></li></ul>
<table><tr><td>姓名</td><td>职称</td></tr><tr><td>张三</td><td>副教授</td></tr></table>
<a href="javascript:void(0)">下一页</a><a href="#top">top</a>
<div class="footer">版权所有</div></body></html>"""


def test_markdown_keeps_links_absolute_and_drops_noise():
    r = html_to_markdown(PAGE, "https://www.example.edu.cn/szdw/list.htm")
    md = r.markdown
    assert md.startswith("# 教职工名录 - 计算机系")
    assert "[蒋　力](https://www.example.edu.cn/info/1/2.htm)" in md          # absolute URL, U+3000 kept
    assert "[li@x.edu.cn](mailto:li@x.edu.cn)" in md
    assert "## 教授" in md and "| 姓名 | 职称 |" in md and "| 张三 | 副教授 |" in md
    for noise in ("var x", "版权所有", "当前位置", "新闻", ".a{}"):
        assert noise not in md, noise
    assert "javascript" not in md and "(#top)" not in md
    assert not r.fallback and r.title == "教职工名录 - 计算机系"


def test_safety_net_when_noise_removal_eats_the_content():
    body = "<div class='menu'>" + "<p>真实内容 教授 张三 李四</p>" * 300 + "</div>"
    r = html_to_markdown("<html><body>" + body + "</body></html>", "https://a.edu.cn/")
    assert r.fallback and "真实内容" in r.markdown


def test_stored_text_is_full_and_truncation_is_for_the_llm_only():
    big = "\n".join(f"- line {i} 教授" for i in range(5000))
    r = html_to_markdown("<html><body>" + "".join(f"<p>line {i} 教授</p>" for i in range(5000)) + "</body></html>")
    assert len(r.markdown) > 50_000                                         # nothing truncated in storage
    cut = truncate_for_llm(r.markdown, 2000)
    assert len(cut) < 2300 and "[... truncated:" in cut and cut.count("\n") < r.markdown.count("\n")
    assert truncate_for_llm("short", 2000) == "short"


def test_real_saved_pages_are_cleaned_without_losing_the_content():
    sizes = {}
    for name in ("bupt_teacher_en_home.html", "cqu_faculties_and_schools.html", "sjtu_rendered.html"):
        r = html_to_markdown(fixture(name), "https://x.edu.cn/")
        sizes[name] = (r.chars_before, r.chars_after)
        assert r.chars_after > 100 and "<script" not in r.markdown and "<div" not in r.markdown
    assert "陈海波" in html_to_markdown(fixture("sjtu_rendered.html")).markdown
    assert "School of Computer Science" in html_to_markdown(fixture("cqu_faculties_and_schools.html")).markdown
