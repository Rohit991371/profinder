import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fetch_helpers import FakeClient
from profinder_pipeline.fetch.cache import PageCache, md5_text, url_hash
from profinder_pipeline.fetch.engine import FetchedPage
from profinder_pipeline.fetch import store


def test_write_read_roundtrip_and_ttl(tmp_path):
    now = [datetime(2026, 10, 22, tzinfo=timezone.utc)]
    c = PageCache(tmp_path, now=lambda: now[0])
    assert c.read("https://a.edu.cn/x") is None
    c.write("https://a.edu.cn/x", "https://a.edu.cn/x", "<html>页面</html>", 200, "playwright", "utf-8", "abc")
    p = c.read("https://a.edu.cn/x")
    assert p.html == "<html>页面</html>" and p.render_mode == "playwright" and p.content_hash == "abc"
    now[0] += timedelta(days=29)
    assert c.read("https://a.edu.cn/x") is not None
    now[0] += timedelta(days=2)
    assert c.read("https://a.edu.cn/x") is None                       # older than 30 days
    assert c.read("https://a.edu.cn/x", allow_stale=True) is not None


def test_reads_files_written_by_discovery(tmp_path):
    k = url_hash("https://a.edu.cn/old")
    (tmp_path / f"{k}.html").write_text("<html>old</html>", encoding="utf-8")
    (tmp_path / f"{k}.json").write_text(json.dumps({"url": "https://a.edu.cn/old", "final_url": "https://a.edu.cn/old", "status": 200,
                                                   "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")
    p = PageCache(tmp_path).read("https://a.edu.cn/old")
    assert p.render_mode == "static" and p.content_hash is None and p.html == "<html>old</html>"


def test_key_is_md5_of_url_and_no_tmp_files_left(tmp_path):
    c = PageCache(tmp_path)
    c.write("https://a.edu.cn/x", "https://a.edu.cn/x", "h", 200, "static", "utf-8", md5_text("h"))
    assert (tmp_path / f"{url_hash('https://a.edu.cn/x')}.html").is_file()
    assert not list(tmp_path.glob("*.tmp"))
    assert len(url_hash("u")) == 32


class FakeConn:
    """Records SQL; answers the 'latest content_hash' query from a dict."""
    def __init__(self, latest=None):
        self.latest, self.sql = latest or {}, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        conn = self

        class Cur:
            def fetchone(self_inner):
                if "select content_hash" in sql:
                    h = conn.latest.get(params[0])
                    return (h,) if h else None
        return Cur()

    def commit(self):
        pass


def _page(**kw):
    base = dict(url="https://a.edu.cn/x", final_url="https://a.edu.cn/x", status=200, html="<p>x</p>", markdown="x", render_mode="static",
                encoding="utf-8", content_hash="h1", fetched_at=datetime.now(timezone.utc), from_cache=False)
    base.update(kw)
    return FetchedPage(**base)


def test_persist_page_inserts_real_fetches_and_skips_unchanged_cache_hits():
    conn = FakeConn()
    assert store.persist_page(conn, _page(), "t1") == "inserted"
    assert sum("insert into ingest.page" in s for s, _ in conn.sql) == 1
    conn2 = FakeConn({url_hash("https://a.edu.cn/x"): "h1"})
    assert store.persist_page(conn2, _page(from_cache=True), "t1") == "unchanged"          # second run writes nothing
    assert not any("insert" in s for s, _ in conn2.sql)
    conn3 = FakeConn()                                                                      # cache hit, DB has no copy yet (discovery cache)
    assert store.persist_page(conn3, _page(from_cache=True), "t1") == "inserted"


def test_persist_page_error_rows_only_for_network_attempts():
    conn = FakeConn()
    failed = FetchedPage("https://a.edu.cn/x", error="http_412", status=412, network=True)
    assert store.persist_page(conn, failed, "t1") == "inserted"
    assert store.persist_page(FakeConn(), FetchedPage("https://a.edu.cn/x", error="budget"), "t1") == "skipped"
    assert store.persist_page(FakeConn(), FetchedPage("https://a.edu.cn/x", error="off_domain"), None) == "skipped"
