"""Groq plumbing without pydantic (task 4.2): JSON repair, token estimate, rate limiter, 429/5xx back-off, variant fallback,
token log. The model name comes from GROQ_MODEL in .env (never hard-coded here); the key is read from the environment and never
logged. Free-tier note: this account showed 8,000 tokens/minute, so the limiter keeps a safety margin below that."""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

API_URL = "https://api.groq.com/openai/v1/chat/completions"
PRICE_IN_PER_M = 0.80       # USD per 1M tokens, from the spike (Groq pricing page for this model); used only for the cost log
PRICE_OUT_PER_M = 4.00
_CJK = re.compile(r"[\u3400-\u9fff]")


# ----------------------------------------------------------------------------------------------- JSON repair
def strip_wrappers(text: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.I)
    return t.strip()


def repair_json(text: str) -> Optional[str]:
    """Best-effort fix of model output that is almost JSON: fences/think tags, text around the object, trailing commas,
    truncated output (closes open strings/brackets). Returns a JSON string or None. Never invents values."""
    t = strip_wrappers(text)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        return None
    t = t[min(starts):]
    for candidate in (t, _trim_to_balanced(t), _close_open(t)):
        if candidate is None:
            continue
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            json.loads(candidate)
            return candidate
        except Exception:  # noqa: BLE001
            continue
    return None


def _trim_to_balanced(t: str) -> Optional[str]:
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return t[: i + 1]
    return None


def _close_open(t: str) -> Optional[str]:
    """Output cut off by max_tokens: drop the dangling fragment and close brackets."""
    stack: list[str] = []
    in_str, esc = False, False
    last_good = 0
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            last_good = i + 1
        elif ch == ",":
            last_good = i
    if not stack:
        return None
    body = t[:last_good] if in_str else t
    body = re.sub(r",\s*$", "", body.rstrip())
    return body + "".join(reversed(stack))


# ----------------------------------------------------------------------------------------------- tokens / chunks
def estimate_tokens(text: str) -> int:
    """Conservative: ~0.9 token per CJK char, ~0.3 per other char (+ prompt overhead added by the caller)."""
    cjk = len(_CJK.findall(text or ""))
    return int(cjk * 0.9 + (len(text or "") - cjk) * 0.3) + 1


def chunk_by_items(text: str, max_chars: int = 3500) -> list[str]:
    """Split page text into chunks at item boundaries (blank line or a list-item line), never inside an item."""
    blocks = [b for b in re.split(r"\n\s*\n|\n(?=[-*•] )", text or "") if b.strip()]
    chunks: list[str] = []
    cur = ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > max_chars:
            chunks.append(cur)
            cur = ""
        if len(b) > max_chars:                      # one huge block: hard split on line boundaries
            for line in b.split("\n"):
                if cur and len(cur) + len(line) + 1 > max_chars:
                    chunks.append(cur)
                    cur = ""
                cur += ("\n" if cur else "") + line
            continue
        cur += ("\n\n" if cur else "") + b
    if cur:
        chunks.append(cur)
    return chunks


# ----------------------------------------------------------------------------------------------- rate limiter
class TokenLimiter:
    """Sliding 60 s window over tokens and requests. wait(n) sleeps until a call of n tokens fits."""

    def __init__(self, tpm: int = 6500, rpm: int = 25, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.tpm, self.rpm, self._clock, self._sleep = tpm, rpm, clock, sleep
        self._events: list[tuple[float, int]] = []

    def _prune(self) -> None:
        cut = self._clock() - 60.0
        self._events = [(t, n) for t, n in self._events if t > cut]

    def wait(self, tokens: int) -> float:
        waited = 0.0
        tokens = min(tokens, self.tpm)       # a single call larger than the budget can never fit; let it run alone
        while True:
            self._prune()
            used = sum(n for _, n in self._events)
            if used + tokens <= self.tpm and len(self._events) < self.rpm:
                return waited
            oldest = self._events[0][0] if self._events else self._clock()
            delay = max(0.5, oldest + 60.0 - self._clock())
            self._sleep(delay)
            waited += delay

    def record(self, tokens: int) -> None:
        self._events.append((self._clock(), tokens))

    def penalise(self, seconds: float) -> None:
        """After a 429: pretend the window is full for `seconds`."""
        self._events.append((self._clock() - 60.0 + seconds, self.tpm))


# ----------------------------------------------------------------------------------------------- client
@dataclass
class LlmReply:
    text: str
    prompt_tokens: int
    completion_tokens: int
    variant: str
    seconds: float


class LlmError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind


Transport = Callable[[dict[str, Any], dict[str, str], float], tuple[int, dict[str, Any], dict[str, str]]]


def httpx_transport(payload: dict[str, Any], headers: dict[str, str], timeout: float) -> tuple[int, dict[str, Any], dict[str, str]]:
    import httpx  # imported lazily so tests and offline commands do not need it
    r = httpx.post(API_URL, json=payload, headers=headers, timeout=timeout)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = {"error": {"message": r.text[:300]}}
    return r.status_code, body, {k.lower(): v for k, v in r.headers.items()}


@dataclass
class GroqClient:
    api_key: str = field(repr=False)
    model: str
    transport: Transport = httpx_transport
    limiter: TokenLimiter = field(default_factory=TokenLimiter)
    log_path: Optional[Path] = None
    sleep: Callable[[float], None] = time.sleep
    max_attempts: int = 4
    timeout: float = 60.0
    _variant: int = 0
    totals: dict[str, float] = field(default_factory=lambda: {"calls": 0, "prompt": 0, "completion": 0, "usd": 0.0})

    def _variants(self, schema: Optional[dict[str, Any]], schema_name: str, max_tokens: int) -> list[tuple[str, dict[str, Any]]]:
        base: dict[str, Any] = {"model": self.model, "temperature": 0, "max_tokens": max_tokens}
        out: list[tuple[str, dict[str, Any]]] = []
        if schema is not None:
            rf_strict = {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
            rf_loose = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}}
            out += [("strict+instruct", {**base, "response_format": rf_strict, "reasoning_effort": "none"}),
                    ("loose+instruct", {**base, "response_format": rf_loose, "reasoning_effort": "none"}),
                    ("loose", {**base, "response_format": rf_loose}),
                    ("json_object", {**base, "response_format": {"type": "json_object"}})]
        else:
            out.append(("plain", base))
        return out

    def chat_json(self, system: str, user: str, schema: Optional[dict[str, Any]], schema_name: str, purpose: str,
                  ref: str = "", max_tokens: int = 1500) -> LlmReply:
        variants = self._variants(schema, schema_name, max_tokens)
        est = estimate_tokens(system) + estimate_tokens(user) + max_tokens // 4 + 250
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        attempt, vi = 0, min(self._variant, len(variants) - 1)
        last = "no attempt"
        while attempt < self.max_attempts:
            attempt += 1
            name, extra = variants[vi]
            payload = {**extra, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            self.limiter.wait(est)
            t0 = time.monotonic()
            status, body, hdrs = self.transport(payload, headers, self.timeout)
            secs = time.monotonic() - t0
            if status == 200:
                usage = body.get("usage") or {}
                pt, ct = int(usage.get("prompt_tokens", est)), int(usage.get("completion_tokens", 0))
                self.limiter.record(pt + ct)
                self._variant = vi
                text = (((body.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
                self._log(purpose, ref, name, "ok", pt, ct, secs)
                return LlmReply(text, pt, ct, name, secs)
            msg = str((body.get("error") or {}).get("message", ""))[:200]
            last = f"HTTP {status} {msg}"
            self._log(purpose, ref, name, f"http_{status}", 0, 0, secs)
            if status == 429:
                wait = _retry_after(hdrs, default=min(30.0, 5.0 * attempt))
                self.limiter.penalise(wait)
                self.sleep(wait)
                continue
            if status in (500, 502, 503, 504):
                self.sleep(min(30.0, 2.0 * 2 ** (attempt - 1)))
                continue
            if status in (400, 422) and vi < len(variants) - 1:    # response_format / reasoning not accepted: next variant
                vi += 1
                continue
            raise LlmError(f"http_{status}", msg or "request refused")
        raise LlmError("gave_up", last)

    def _log(self, purpose: str, ref: str, variant: str, status: str, pt: int, ct: int, secs: float) -> None:
        usd = (pt * PRICE_IN_PER_M + ct * PRICE_OUT_PER_M) / 1_000_000
        if status == "ok":
            self.totals["calls"] += 1
            self.totals["prompt"] += pt
            self.totals["completion"] += ct
            self.totals["usd"] += usd
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": self.model, "purpose": purpose,
                   "ref": ref[:200], "variant": variant, "status": status, "prompt_tokens": pt, "completion_tokens": ct,
                   "est_usd": round(usd, 6), "seconds": round(secs, 2)}
            with self.log_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _retry_after(headers: dict[str, str], default: float) -> float:
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = headers.get(key)
        if not v:
            continue
        m = re.fullmatch(r"\s*(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?\s*", v) or None
        try:
            if m and (m.group(1) or m.group(2)):
                return min(60.0, float(m.group(1) or 0) * 60 + float(m.group(2) or 0) + 0.5)
            return min(60.0, float(v) + 0.5)
        except ValueError:
            continue
    return default
