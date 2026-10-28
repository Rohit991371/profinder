from profinder_pipeline.fetch.static import StaticFetcher


class _Resp:
    def __init__(self, status, url, body=b"<html><body>ok</body></html>"):
        self.status_code, self.url, self.content = status, url, body
        self.headers = {"content-type": "text/html; charset=utf-8"}


class _Client:
    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return _Resp(400, url) if url.startswith("http://") else _Resp(200, url)


class _Throttle:
    def wait(self, host):
        pass


def test_http_400_is_retried_over_https():
    c = _Client()
    res = StaticFetcher(c, _Throttle(), sleep=lambda s: None).get("http://faculty.nuaa.edu.cn/wangran/zh_CN/index.htm")
    assert res.kind == "ok" and res.status == 200
    assert c.calls[-1].startswith("https://faculty.nuaa.edu.cn/")