"""Browser renderer (task 3.2) for JavaScript-built lists and sites that refuse the plain HTTP client.

Plain Playwright (Chromium). Crawl4AI is not used: it adds heavy dependencies and this needs nothing it offers.
The browser keeps its own native user agent (it is a real browser running the site's JavaScript);
robots.txt is still obeyed by the gate, with our own token. Install:  pip install playwright && playwright install chromium"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit, unquote

INSTALL_HINT = "playwright is not installed: run `pip install playwright` then `playwright install chromium`"


def playwright_proxy(proxy_url: Optional[str]) -> Optional[dict]:
    """PROXY_URL -> Playwright proxy dict (credentials split out). Never logged."""
    if not proxy_url:
        return None
    sp = urlsplit(proxy_url)
    out = {"server": f"{sp.scheme}://{sp.hostname}" + (f":{sp.port}" if sp.port else "")}
    if sp.username:
        out["username"] = unquote(sp.username)
    if sp.password:
        out["password"] = unquote(sp.password)
    return out


@dataclass
class RenderResult:
    status: Optional[int]
    final_url: str
    html: str = ""
    error: Optional[str] = None
    notes: list = field(default_factory=list)


class BrowserRenderer:
    name = "playwright"

    def __init__(self, proxy_url: Optional[str] = None, nav_timeout_ms: int = 45_000, headless: bool = True,
                 block_resources: tuple = ("image", "media", "font")) -> None:
        self.proxy = playwright_proxy(proxy_url)
        self.nav_timeout_ms, self.headless, self.block = nav_timeout_ms, headless, set(block_resources)
        self._pw = self._browser = self._context = None

    # ---- lifecycle ----
    @staticmethod
    def installed() -> tuple[bool, str]:
        try:
            import playwright.sync_api  # noqa: F401
            return True, "playwright importable"
        except ImportError:
            return False, INSTALL_HINT

    def _ensure(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        kw = {"headless": self.headless}
        if self.proxy:
            kw["proxy"] = self.proxy
        self._browser = self._pw.chromium.launch(**kw)
        self._context = self._browser.new_context(locale="zh-CN", ignore_https_errors=False)
        block = self.block
        self._context.route("**/*", lambda route: route.abort() if route.request.resource_type in block else route.continue_())

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- work ----
    def render(self, url: str, wait_selector: Optional[str] = None, settle_ms: int = 1000) -> RenderResult:
        from playwright.sync_api import TimeoutError as PWTimeout
        notes: list = []
        try:
            self._ensure()
            page = self._context.new_page()
        except Exception as exc:                                  # browser missing, launch failure ...
            return RenderResult(None, url, "", f"render_unavailable:{type(exc).__name__}")
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeout:
                notes.append("networkidle_timeout")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15_000)
                except PWTimeout:
                    notes.append("selector_timeout")
            page.wait_for_timeout(settle_ms)
            return RenderResult(resp.status if resp else None, page.url, page.content(), None, notes)
        except PWTimeout:
            return RenderResult(None, url, "", "timeout", notes)
        except Exception as exc:
            return RenderResult(None, url, "", f"render_failed:{type(exc).__name__}", notes)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def get_text(self, url: str) -> tuple[Optional[int], str]:
        """Plain text of a URL (robots.txt) through the browser: (status, body)."""
        try:
            self._ensure()
            page = self._context.new_page()
        except Exception:
            return None, ""
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            body = resp.text() if resp else ""
            return (resp.status if resp else None), body
        except Exception:
            return None, ""
        finally:
            try:
                page.close()
            except Exception:
                pass
