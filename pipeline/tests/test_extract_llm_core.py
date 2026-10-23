import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import json

from profinder_pipeline.extract.llm_core import (GroqClient, LlmError, TokenLimiter, chunk_by_items, estimate_tokens, repair_json)


def test_repair_json_variants():
    assert json.loads(repair_json('```json\n{"a": 1, "b": [1,2,],}\n```')) == {"a": 1, "b": [1, 2]}
    assert json.loads(repair_json('<think>x</think> Here you go: {"a": "b"} thanks')) == {"a": "b"}
    assert json.loads(repair_json('{"people":[{"n":"a"},{"n":"b"')) == {"people": [{"n": "a"}, {"n": "b"}]}
    cut = json.loads(repair_json('{"people":[{"n":"a","e":"x@y.com"},{"n":"b","e":"x@'))
    assert cut["people"][0]["e"] == "x@y.com" and cut["people"][1] == {"n": "b"}     # a dangling half value is dropped, not kept
    assert repair_json("no json at all") is None


def test_chunking_keeps_items_whole():
    text = "\n\n".join(f"- item {i} " + "x" * 300 for i in range(30))
    chunks = chunk_by_items(text, 1500)
    assert len(chunks) > 3 and all(len(c) <= 1500 for c in chunks)
    assert "".join(chunks).count("- item") == 30
    assert chunk_by_items("") == []


def test_estimate_tokens_is_conservative_for_chinese():
    assert estimate_tokens("数据库" * 100) >= 250 and estimate_tokens("abc " * 100) < 200


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_limiter_waits_for_the_window():
    c = Clock()
    lim = TokenLimiter(tpm=1000, rpm=100, clock=c.now, sleep=c.sleep)
    assert lim.wait(600) == 0
    lim.record(600)
    waited = lim.wait(600)                     # does not fit with the first one: must wait ~60 s
    assert 59 <= waited <= 62


def _ok(text='{"x": 1}', pt=100, ct=10):
    return 200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}, {}


def _client(responses, tmp_path):
    calls = []

    def transport(payload, headers, timeout):
        calls.append((payload, headers))
        return responses.pop(0)
    c = Clock()
    cl = GroqClient(api_key="gsk_SECRETSECRETSECRET", model="m", transport=transport, limiter=TokenLimiter(clock=c.now, sleep=c.sleep),
                    log_path=tmp_path / "log.jsonl", sleep=c.sleep)
    return cl, calls


def test_429_backoff_then_success_and_log_has_no_secret(tmp_path):
    cl, calls = _client([(429, {"error": {"message": "slow"}}, {"retry-after": "7"}), _ok()], tmp_path)
    r = cl.chat_json("sys", "user", {"type": "object"}, "n", "test", "ref")
    assert r.text == '{"x": 1}' and len(calls) == 2
    log = (tmp_path / "log.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in log and "gsk_" not in log and '"status": "http_429"' in log and '"status": "ok"' in log
    assert cl.totals["calls"] == 1 and cl.totals["prompt"] == 100


def test_variant_fallback_on_400(tmp_path):
    cl, calls = _client([(400, {"error": {"message": "reasoning_effort not supported"}}, {}), _ok()], tmp_path)
    r = cl.chat_json("s", "u", {"type": "object"}, "n", "t", "")
    assert r.variant == "loose+instruct" and "strict" in json.dumps(calls[0][0]) and calls[1][0]["response_format"]["json_schema"].get("strict") is None


def test_gives_up_after_repeated_server_errors(tmp_path):
    cl, _ = _client([(503, {}, {})] * 6, tmp_path)
    try:
        cl.chat_json("s", "u", None, "n", "t", "")
        assert False, "expected LlmError"
    except LlmError as e:
        assert e.kind == "gave_up"
