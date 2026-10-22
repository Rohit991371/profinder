import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from profinder_pipeline.fetch.gate import (Gate, RetryPolicy, Throttle, classify_status, interpret_robots, parse_retry_after)

ROBOTS_BLOCK = "User-agent: *\nDisallow: /private/\n"


def test_throttle_spaces_requests_per_host():
    t = [100.0]
    slept = []
    th = Throttle(2.5, 0.5, lambda: t[0], lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), lambda: 1.0)
    assert th.wait("a.edu.cn") == 0.0              # first request: no wait
    t[0] += 1.0
    assert th.wait("a.edu.cn") == 2.0              # gap 3.0 (2.5 + 0.5 jitter) minus 1 s already elapsed
    assert th.wait("b.edu.cn") == 0.0              # other host independent
    t[0] += 10.0
    assert th.wait("a.edu.cn") == 0.0              # long enough ago
    assert slept == [2.0]


def test_retry_delay_backoff_and_retry_after():
    p = RetryPolicy(3, 2.0, 30.0)
    assert [p.delay_for(1), p.delay_for(2), p.delay_for(3)] == [2.0, 4.0, 8.0]
    assert p.delay_for(9) == 30.0                  # capped
    assert p.delay_for(1, retry_after=7) == 7
    assert p.delay_for(1, retry_after=999) == 30.0
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after(None) is None


def test_classify_status():
    assert classify_status(200) == "ok"
    assert classify_status(202) == "challenge"
    for s in (429, 500, 502, 503, 504):
        assert classify_status(s) == "retry"
    for s in (401, 403, 412, 418):
        assert classify_status(s) == "blocked"
    assert classify_status(404) == "gone" and classify_status(410) == "gone"
    assert classify_status(400) == "error"


def test_interpret_robots_follows_rfc9309():
    assert interpret_robots(404, "")[0] == "none"
    assert interpret_robots(403, "")[0] == "none"                       # 4xx = unavailable = no restriction
    assert interpret_robots(412, "")[0] == "none"
    assert interpret_robots(200, "<html><body>404 not found</body></html>")[0] == "none"
    assert interpret_robots(500, "")[0] == "unreadable"
    assert interpret_robots(503, "")[0] == "unreadable"
    assert interpret_robots(429, "")[0] == "unreadable"
    assert interpret_robots(None, "")[0] == "unreadable"
    kind, rp = interpret_robots(200, ROBOTS_BLOCK)
    assert kind == "parsed" and not rp.can_fetch("ProFinderPipeline/0.1", "https://x.edu.cn/private/a")


def _gate(robots):
    g = Gate(lambda base: robots)
    g.begin(["example.edu.cn"], 3)
    return g


def test_gate_allow_list_and_subdomains():
    g = _gate(("none", None))
    assert g.check("https://www.example.edu.cn/a").allowed
    assert g.check("https://cs.example.edu.cn/a").allowed
    d = g.check("https://evil.com/a")
    assert not d.allowed and d.reason == "off_domain"
    assert not g.check("https://notexample.edu.cn/a").allowed         # suffix trick


def test_gate_robots_disallow_and_unreadable():
    g = _gate(interpret_robots(200, ROBOTS_BLOCK))
    assert g.check("https://example.edu.cn/ok").allowed
    d = g.check("https://example.edu.cn/private/x")
    assert not d.allowed and d.reason == "robots_disallowed" and d.robots_status == "disallowed"
    g2 = _gate(("unreadable", None))
    d2 = g2.check("https://example.edu.cn/x")
    assert not d2.allowed and d2.reason == "robots_unreadable" and d2.robots_status == "unknown"


def test_gate_budget_counts_only_spent_fetches():
    g = _gate(("none", None))
    for _ in range(3):
        assert g.check("https://example.edu.cn/a").allowed
        g.spend()
    d = g.check("https://example.edu.cn/a")
    assert not d.allowed and d.reason == "budget" and g.exhausted
    g.begin(["example.edu.cn"], 3)                                    # next university: reset
    assert g.check("https://example.edu.cn/a").allowed


def test_gate_breaker_opens_after_three_failures_and_resets_on_success():
    g = _gate(("none", None))
    h = "example.edu.cn"
    g.note_failure(h); g.note_failure(h); g.note_success(h); g.note_failure(h); g.note_failure(h)
    assert g.check("https://example.edu.cn/a").allowed
    g.note_failure(h)
    assert g.check("https://example.edu.cn/a").reason == "host_blocked"


def test_gate_static_blocked_after_two_refusals_and_robots_cached():
    g = _gate(("none", None))
    assert g.robots_cached("https://example.edu.cn/a") == "unknown"   # nothing read yet, no network
    g.check("https://example.edu.cn/a")
    assert g.robots_cached("https://example.edu.cn/a") == "allowed"
    h = "example.edu.cn"
    g.note_static_block(h)
    assert not g.static_blocked(h)
    g.note_static_block(h)
    assert g.static_blocked(h)
