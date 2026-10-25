"""Gate (task 3.1): allow-list, robots.txt, per-domain throttle, retry/back-off policy, host circuit breaker, page budget.

Pure logic with injectable clock/sleep, so every rule is unit-tested without a network.
Robots rules follow RFC 9309: no file / any 4xx = no restriction; 5xx or unreachable = treated as disallowed.
The user agent is never disguised."""
from __future__ import annotations

import random
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

from ..registry.textnorm import host_allowed, host_of
from .basic import USER_AGENT

BLOCK_STATUSES = frozenset({401, 403, 412, 418, 451})      # refusal / bot protection: not retried with the same client
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})       # transient: retried with back-off
CHALLENGE_STATUSES = frozenset({202})                       # sites that answer 202 with a "verifying you" page


class Throttle:
    """At least delay (+ random jitter) seconds between two requests to the same host."""

    def __init__(self, delay: float = 2.5, jitter: float = 0.5, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random,
                 slow_after: Optional[float] = None, max_factor: float = 8.0) -> None:
        self.delay, self.jitter, self._clock, self._sleep, self._rng = delay, jitter, clock, sleep, rng
        self.slow_after, self.max_factor = slow_after, max_factor     # slow_after=None: no adaptation (the old behaviour)
        self._last: dict[str, float] = {}
        self._factor: dict[str, float] = {}

    def factor(self, host: str) -> float:
        return self._factor.get(host, 1.0)

    def observe(self, host: str, seconds: float, ok: bool = True) -> None:
        """Adaptive delay (Iteration 6): a slow or failed answer doubles the gap to that host (up to max_factor x);
        a fast answer halves it again (down to 1x). Politeness only ever increases under load."""
        if self.slow_after is None:
            return
        f = self._factor.get(host, 1.0)
        if not ok or seconds >= self.slow_after:
            f = min(f * 2.0, self.max_factor)
        elif seconds < self.slow_after / 2.0:
            f = max(f / 2.0, 1.0)
        self._factor[host] = f

    def wait(self, host: str) -> float:
        slept = 0.0
        last = self._last.get(host)
        if last is not None:
            gap = (self.delay + self.jitter * self._rng()) * self.factor(host)
            slept = max(0.0, gap - (self._clock() - last))
            if slept > 0:
                self._sleep(slept)
        self._last[host] = self._clock() + slept
        return slept


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 2.0
    max_delay: float = 30.0

    def delay_for(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based). A server's Retry-After wins, capped."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds form only; the HTTP-date form is ignored (-> default back-off)."""
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def classify_status(status: int) -> str:
    """ok | challenge | retry | blocked | gone | error"""
    if status == 200:
        return "ok"
    if status in CHALLENGE_STATUSES:
        return "challenge"
    if status in RETRY_STATUSES:
        return "retry"
    if status in BLOCK_STATUSES:
        return "blocked"
    if status in (404, 410):
        return "gone"
    return "error"


def interpret_robots(status: Optional[int], body: str):
    """-> (kind, parser). kind: 'none' (no restriction) | 'parsed' | 'unreadable' (treated as not allowed)."""
    if status is None:
        return "unreadable", None
    if status == 200:
        if "<html" in body[:1000].lower():
            return "none", None                      # an HTML error page served instead of a robots file
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        return "parsed", rp
    if status == 429 or 500 <= status <= 599:
        return "unreadable", None
    return "none", None                              # 404/410/403/412 ...: unavailable = no restriction (RFC 9309)


@dataclass
class Decision:
    allowed: bool
    reason: str = "ok"                # ok | off_domain | robots_disallowed | robots_unreadable | host_blocked | budget
    robots_status: str = "unknown"    # allowed | disallowed | unknown  (ingest.target.robots_status)


class Gate:
    """State for one university at a time: begin() resets domains, budget and the per-host breakers.
    The robots cache lives for the whole run (hosts differ between universities)."""

    def __init__(self, robots_reader: Callable[[str], tuple], breaker: int = 3, static_block_limit: int = 2,
                 cooldown: Optional[float] = None, max_cooldown: float = 1800.0, max_wait_total: float = 900.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._reader = robots_reader               # base_url -> (kind, RobotFileParser|None)
        self._robots: dict[str, tuple] = {}
        self.breaker, self.static_block_limit = breaker, static_block_limit
        # cooldown=None keeps the old rule (a tripped host stays blocked until the next university). With a cooldown (seconds),
        # a tripped host is probed again after it (doubling at every further trip, capped): one half-open request decides.
        self.cooldown, self.max_cooldown, self.max_wait_total, self._clock = cooldown, max_cooldown, max_wait_total, clock
        self.begin([], 0)

    def begin(self, domains: list[str], budget: int) -> None:
        self.domains, self.budget, self.used, self.exhausted = list(domains), budget, 0, False
        self._fails: dict[str, int] = {}
        self._static_blocks: dict[str, int] = {}
        self._open: dict[str, float] = {}          # host -> time the breaker may be probed again (inf = until the next university)
        self._trips: dict[str, int] = {}
        self.waited = 0.0

    # ---- decisions ----
    def on_domain(self, url: str) -> bool:
        return host_allowed(host_of(url), self.domains)

    def robots(self, url: str) -> Decision:
        sp = urlsplit(url)
        base = f"{sp.scheme}://{sp.netloc}"
        if base not in self._robots:
            self._robots[base] = self._reader(base)
        kind, rp = self._robots[base]
        if kind == "none":
            return Decision(True, "ok", "allowed")
        if kind == "parsed":
            ok = rp.can_fetch(USER_AGENT, url)
            return Decision(ok, "ok" if ok else "robots_disallowed", "allowed" if ok else "disallowed")
        return Decision(False, "robots_unreadable", "unknown")

    def robots_cached(self, url: str) -> str:
        """allowed | disallowed | unknown from what was already read (never touches the network)."""
        sp = urlsplit(url)
        entry = self._robots.get(f"{sp.scheme}://{sp.netloc}")
        if entry is None:
            return "unknown"
        return self.robots(url).robots_status

    def check(self, url: str) -> Decision:
        """Everything that must pass before a NETWORK fetch (cache hits only need on_domain)."""
        if not self.on_domain(url):
            return Decision(False, "off_domain")
        host = host_of(url)
        if host in self._open:
            if self._clock() >= self._open[host]:
                del self._open[host]               # half-open: one request decides; a failure trips it again, for longer
                self._fails[host] = self.breaker - 1
            else:
                return Decision(False, "host_blocked")
        d = self.robots(url)
        if not d.allowed:
            return d
        if self.used >= self.budget:
            self.exhausted = True
            return Decision(False, "budget", d.robots_status)
        return d

    # ---- accounting ----
    def spend(self, n: int = 1) -> None:
        self.used += n

    def note_success(self, host: str) -> None:
        self._fails[host] = 0

    def note_failure(self, host: str) -> None:
        self._fails[host] = self._fails.get(host, 0) + 1
        if self._fails[host] >= self.breaker:
            self._trips[host] = self._trips.get(host, 0) + 1
            if self.cooldown:
                wait = min(self.cooldown * (2 ** (self._trips[host] - 1)), self.max_cooldown)
                self._open[host] = self._clock() + wait
            else:
                self._open[host] = float("inf")

    def cooldown_remaining(self, host: str) -> float:
        """Seconds until a tripped host may be probed again (0 = not tripped, or blocked until the next university)."""
        t = self._open.get(host)
        if t is None or t == float("inf"):
            return 0.0
        return max(0.0, t - self._clock())

    def may_wait(self, seconds: float) -> bool:
        """The engine sleeps through a cooldown only while the per-university waiting budget lasts."""
        return self.cooldown is not None and self.waited + seconds <= self.max_wait_total

    def note_waited(self, seconds: float) -> None:
        self.waited += seconds

    def note_static_block(self, host: str) -> None:
        self._static_blocks[host] = self._static_blocks.get(host, 0) + 1

    def static_blocked(self, host: str) -> bool:
        """After a few refusals the plain client is skipped for this host; the browser is used directly."""
        return self._static_blocks.get(host, 0) >= self.static_block_limit
