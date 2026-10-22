import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from fetch_helpers import Clock, FakeClient, FakeResponse
from profinder_pipeline.fetch.gate import RetryPolicy, Throttle
from profinder_pipeline.fetch.static import StaticFetcher, client_kwargs, decode_html

HTML = "<html><head><title>t</title></head><body>" + "内容 " * 50 + "</body></html>"


def _fetcher(routes):
    c = Clock()
    client = FakeClient(routes)
    f = StaticFetcher(client, Throttle(2.5, 0.0, c.now, c.sleep, lambda: 0.0), RetryPolicy(3, 2.0, 30.0), c.sleep)
    return f, client, c


def test_decode_gbk_gb2312_utf8_bom_and_lying_header():
    gbk = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"></head><body>清华大学计算机系 教授</body></html>').encode("gbk")
    text, enc = decode_html(gbk)
    assert "清华大学计算机系" in text and enc == "gb18030"
    text, enc = decode_html(gbk, "text/html")
    assert "教授" in text
    text, enc = decode_html("\ufeff清华".encode("utf-8"))
    assert text == "清华" and enc == "utf-8"
    text, enc = decode_html("计算机学院".encode("utf-8"), "text/html; charset=ISO-8859-1")   # header lies, bytes are utf-8
    assert text == "计算机学院"
    text, enc = decode_html("教授".encode("gbk"), "text/html; charset=utf-8")                 # header lies the other way
    assert text == "教授"


def test_ok_response():
    f, client, _ = _fetcher({"https://a.edu.cn/": FakeResponse(200, HTML)})
    r = f.get("https://a.edu.cn/")
    assert r.kind == "ok" and "内容" in r.html and r.attempts == 1


def test_timeout_then_success_retries_with_backoff():
    f, client, c = _fetcher({"https://a.edu.cn/": [httpx.ConnectTimeout("t"), FakeResponse(200, HTML)]})
    r = f.get("https://a.edu.cn/")
    assert r.kind == "ok" and r.attempts == 2 and c.sleeps[:1] == [2.0] and len(client.calls) == 2


def test_503_three_times_gives_up():
    f, client, c = _fetcher({"https://a.edu.cn/": FakeResponse(503, "busy")})
    r = f.get("https://a.edu.cn/")
    assert r.kind == "http_error" and r.status == 503 and len(client.calls) == 3
    assert [s for s in c.sleeps if s in (2.0, 4.0)] == [2.0, 4.0]


def test_429_honours_retry_after():
    f, client, c = _fetcher({"https://a.edu.cn/": [FakeResponse(429, "slow", {"retry-after": "7"}), FakeResponse(200, HTML)]})
    r = f.get("https://a.edu.cn/")
    assert r.kind == "ok" and 7.0 in c.sleeps


def test_403_412_202_and_404_are_not_retried():
    for status, kind in ((403, "blocked"), (412, "blocked"), (202, "challenge"), (404, "gone")):
        f, client, _ = _fetcher({"https://a.edu.cn/": FakeResponse(status, "x")})
        r = f.get("https://a.edu.cn/")
        assert r.kind == kind and r.status == status and len(client.calls) == 1, status


def test_connection_errors_exhaust_retries():
    f, client, _ = _fetcher({"https://a.edu.cn/": httpx.ConnectError("boom")})
    r = f.get("https://a.edu.cn/")
    assert r.kind == "connect_error" and len(client.calls) == 3


def test_not_html_is_rejected():
    f, _, _ = _fetcher({"https://a.edu.cn/x.pdf": FakeResponse(200, b"%PDF", {"content-type": "application/pdf"})})
    assert f.get("https://a.edu.cn/x.pdf").kind == "not_html"


def test_get_text_for_robots():
    f, _, _ = _fetcher({"https://a.edu.cn/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /x", {"content-type": "text/plain"})})
    status, body, kind = f.get_text("https://a.edu.cn/robots.txt")
    assert status == 200 and "Disallow" in body and kind == "ok"


def test_client_kwargs_proxy_only_when_given_and_honest_user_agent():
    kw = client_kwargs(None)
    assert "proxy" not in kw and kw["headers"]["User-Agent"].startswith("ProFinderPipeline")
    assert client_kwargs("http://127.0.0.1:8080")["proxy"] == "http://127.0.0.1:8080"
