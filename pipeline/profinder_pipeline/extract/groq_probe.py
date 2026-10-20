"""Groq smoke test promoted from the spike: one profile page -> research interests with a strict JSON schema.
The API key is read from the environment and never printed or logged. Iteration 4 builds the real extractor on this."""
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from ..fetch.basic import decode_bytes
from ..logging_setup import redact
from ..paths import INPUTS_DIR, OUTPUT_DIR
from ..settings import get_settings
from .tsinghua_list import visible_text

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# USD per 1M tokens for qwen/qwen3.8-27b, from console.groq.com/docs/models on 2026-10-19. Re-check before relying on it.
PRICE_IN, PRICE_OUT = 0.80, 4.00
MAX_PAGE_CHARS = 8000  # truncation applies to the LLM input only; full text stays in the page store
OUT_DIR = OUTPUT_DIR / "trial"

SYSTEM = (
    "You extract data from a Chinese university faculty profile page. Reply with JSON only. "
    "research_interests: copy the person's own research-interest text from the page verbatim "
    "(keep the original language, do not translate or summarise), or null if the page has none. "
    "evidence_quote: one short exact quote (max 200 characters) from the page that supports it, or null."
)


class GroqInterests(BaseModel):
    """Schema sent to Groq. Every field is required (nullable) so strict mode accepts it. No dict fields."""
    model_config = ConfigDict(extra="forbid")

    research_interests: Optional[str]
    evidence_quote: Optional[str]


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def request_variants(model: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    base = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 1000}

    def fmt(strict: bool) -> dict[str, Any]:
        return {"type": "json_schema", "json_schema": {"name": "profile_interests", "strict": strict, "schema": schema}}
    return [
        ("json_schema strict + reasoning_effort=none", {**base, "response_format": fmt(True), "reasoning_effort": "none"}),
        ("json_schema non-strict + reasoning_effort=none", {**base, "response_format": fmt(False), "reasoning_effort": "none"}),
        ("json_schema non-strict, no reasoning param", {**base, "response_format": fmt(False)}),
    ]


def call_groq(api_key: str, payload: dict[str, Any], max_retries: int = 4) -> httpx.Response:
    """POST with 429 back-off (the free tier is 8,000 tokens/minute on this account)."""
    delay = 5.0
    r = None
    for attempt in range(max_retries + 1):
        r = httpx.post(API_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=90.0)
        if r.status_code != 429 or attempt == max_retries:
            return r
        wait = float(r.headers.get("retry-after", delay))
        print(f"429 rate limited; waiting {wait:.0f}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(wait)
        delay *= 2
    return r  # type: ignore[return-value]


def run_probe(html_path: Path = INPUTS_DIR / "tsinghua_prof1.html") -> dict[str, Any]:
    s = get_settings()
    if not s.groq_api_key or not s.groq_model:
        raise SystemExit("GROQ_API_KEY and GROQ_MODEL must be set in pipeline/.env")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html, _ = decode_bytes(Path(html_path).read_bytes())
    page_text = visible_text(html)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Page text:\n" + page_text[:MAX_PAGE_CHARS]}]
    schema = _strip_titles(GroqInterests.model_json_schema())

    result: dict[str, Any] = {"model": s.groq_model, "page_chars": len(page_text),
                              "sent_chars": min(len(page_text), MAX_PAGE_CHARS), "attempts": []}
    for label, payload in request_variants(s.groq_model, messages, schema):
        t0 = time.monotonic()
        r = call_groq(s.groq_api_key, payload)
        entry: dict[str, Any] = {"variant": label, "http_status": r.status_code, "seconds": round(time.monotonic() - t0, 2)}
        result["attempts"].append(entry)
        if r.status_code != 200:
            entry["error"] = redact(r.text[:300])
            print(f"[{label}] HTTP {r.status_code}: {entry['error']}")
            continue
        body = r.json()
        content = re.sub(r"<think>.*?</think>", "", body["choices"][0]["message"].get("content") or "", flags=re.S).strip()
        usage = body.get("usage", {})
        result["rate_limit_headers"] = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        result["usage"] = {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
        if usage.get("prompt_tokens") is not None:
            result["estimated_cost_usd"] = round(usage["prompt_tokens"] / 1e6 * PRICE_IN + usage.get("completion_tokens", 0) / 1e6 * PRICE_OUT, 6)
        try:
            parsed = GroqInterests.model_validate_json(content)
        except Exception as exc:  # noqa: BLE001 - smoke test: report any validation failure
            entry["valid_schema"] = False
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            print(f"[{label}] returned invalid JSON for the schema: {entry['error']}")
            continue
        entry["valid_schema"] = True
        text_n = _norm(page_text)
        result.update({
            "working_variant": label, "valid_json": True,
            "research_interests": parsed.research_interests, "evidence_quote": parsed.evidence_quote,
            "interests_grounded": None if parsed.research_interests is None else _norm(parsed.research_interests) in text_n,
            "evidence_grounded": None if parsed.evidence_quote is None else _norm(parsed.evidence_quote) in text_n,
        })
        break
    else:
        result["valid_json"] = False
    (OUT_DIR / "groq_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
