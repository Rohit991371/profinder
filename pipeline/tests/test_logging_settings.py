import logging

from profinder_pipeline.logging_setup import RedactingFilter, redact
from profinder_pipeline.settings import Settings


def test_redact_known_secrets_urls_and_tokens():
    text = "key=abcd1234secret url=postgresql://pipeline:hunter22@localhost:55432/db Authorization: Bearer xyz.123-abc gsk_AbCdEf123456789"
    out = redact(text, secrets=["abcd1234secret"])
    assert "abcd1234secret" not in out and "hunter22" not in out and "xyz.123-abc" not in out and "AbCdEf123456789" not in out
    assert "postgresql://***@localhost:55432/db" in out


def test_redact_ignores_tiny_secrets():
    assert redact("a b c", secrets=["a"]) == "a b c"


def test_filter_masks_log_records(monkeypatch=None):
    import os
    os.environ["GROQ_API_KEY"] = "topsecretvalue99"
    try:
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "calling with %s", ("topsecretvalue99",), None)
        assert RedactingFilter().filter(rec)
        assert "topsecretvalue99" not in rec.getMessage() and "***" in rec.getMessage()
    finally:
        del os.environ["GROQ_API_KEY"]


def test_settings_repr_hides_values():
    s = Settings(pipeline_database_url="postgresql://u:pw123456@h/db", groq_api_key="gsk_abcdefghij")
    assert "pw123456" not in repr(s) and "gsk_abcdefghij" not in repr(s)
