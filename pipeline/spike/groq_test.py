"""Task 0.4 — Groq smoke test: one profile page -> research interests with a strict schema.

Usage (from pipeline/):  python -m spike.groq_test [--html inputs/tsinghua_prof1.html]
Needs GROQ_API_KEY and GROQ_MODEL in pipeline/.env. The key is never printed or logged.
Writes output/spike/groq_result.json.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

from .common import INPUTS_DIR, OUT_DIR, ensure_dirs, env
from .fetch import decode_bytes
from .htmlparse import visible_text
from .models import GroqInterests

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# USD per 1M tokens for qwen/qwen3.8-27b, from console.groq.com/docs/models on 2026-10-19. Re-check before relying on it.
PRICE_IN, PRICE_OUT = 0.80, 4.00
MAX_PAGE_CHARS = 8000  # truncation applies to the LLM input only; full text stays in the page store

SYSTEM = (
    "You extract data from a Chinese university faculty profile page. Reply with JSON only. "
    "research_interests: copy the person's own research-interest text from the page verbatim "
    "(keep the original language, do not translate or summarise), or null if the page has none. "
    "evidence_quote: one short exact quote (max 200 characters) from the page that supports it, or null."
)


def _strip_titles(node):
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _variants(model: str, messages: list[dict[str, str]], schema: dict) -> list[tuple[str, dict]]:
    base = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 1000}
    def fmt(strict: bool) -> dict:
        return {"type": "json_schema", "json_schema": {"name": "profile_interests", "strict": strict, "schema": schema}}
    return [
        ("json_schema strict + reasoning_effort=none", {**base, "response_format": fmt(True), "reasoning_effort": "none"}),
        ("json_schema non-strict + reasoning_effort=none", {**base, "response_format": fmt(False), "reasoning_effort": "none"}),
        ("json_schema non-strict, no reasoning param", {**base, "response_format": fmt(False)}),
    ]


def call_groq(api_key: str, payload: dict, max_retries: int = 4) -> httpx.Response:
    delay = 5.0
    for attempt in range(max_retries + 1):
        r = httpx.post(API_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=90.0)
        if r.status_code != 429 or attempt == max_retries:
            return r
        wait = float(r.headers.get("retry-after", delay))
        print(f"429 rate limited; waiting {wait:.0f}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(wait)
        delay *= 2
    return r  # pragma: no cover


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", default=str(INPUTS_DIR / "tsinghua_prof1.html"))
    args = ap.parse_args()
    ensure_dirs()
    api_key, model = env("GROQ_API_KEY"), env("GROQ_MODEL")
    if not api_key or not model:
        raise SystemExit("GROQ_API_KEY and GROQ_MODEL must be set in pipeline/.env")

    html, _ = decode_bytes(Path(args.html).read_bytes())
    page_text = visible_text(html)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Page text:\n" + page_text[:MAX_PAGE_CHARS]}]
    schema = _strip_titles(GroqInterests.model_json_schema())

    result: dict[str, object] = {"model": model, "page_chars": len(page_text), "sent_chars": min(len(page_text), MAX_PAGE_CHARS),
                                 "attempts": []}
    for label, payload in _variants(model, messages, schema):
        t0 = time.monotonic()
        r = call_groq(api_key, payload)
        seconds = round(time.monotonic() - t0, 2)
        entry: dict[str, object] = {"variant": label, "http_status": r.status_code, "seconds": seconds}
        result["attempts"].append(entry)  # type: ignore[union-attr]
        if r.status_code != 200:
            entry["error"] = r.text[:300].replace(api_key, "***")
            print(f"[{label}] HTTP {r.status_code}: {entry['error']}")
            continue
        body = r.json()
        content = re.sub(r"<think>.*?</think>", "", body["choices"][0]["message"].get("content") or "", flags=re.S).strip()
        usage = body.get("usage", {})
        result["rate_limit_headers"] = {k: v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        result["usage"] = {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
        if usage.get("prompt_tokens") is not None:
            result["estimated_cost_usd"] = round(
                usage["prompt_tokens"] / 1e6 * PRICE_IN + usage.get("completion_tokens", 0) / 1e6 * PRICE_OUT, 6)
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
            "working_variant": label,
            "valid_json": True,
            "research_interests": parsed.research_interests,
            "evidence_quote": parsed.evidence_quote,
            "interests_grounded": None if parsed.research_interests is None else _norm(parsed.research_interests) in text_n,
            "evidence_grounded": None if parsed.evidence_quote is None else _norm(parsed.evidence_quote) in text_n,
        })
        break
    else:
        result["valid_json"] = False

    (OUT_DIR / "groq_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("valid_json:", result.get("valid_json"), "| variant:", result.get("working_variant"))
    print("usage:", result.get("usage"), "| est. cost USD:", result.get("estimated_cost_usd"))
    print("interests:", result.get("research_interests"), "| grounded:", result.get("interests_grounded"))
    print("rate-limit headers:", result.get("rate_limit_headers"))


if __name__ == "__main__":
    main()
