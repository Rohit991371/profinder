"""Email extraction, de-obfuscation and the domain rule (doc 02 section 4.1)."""
import re
from typing import Iterable, Optional

EMAIL_FIND_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_STRICT_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

_BRACKET_AT = re.compile(r"\s*[\[\(\{（【]\s*at\s*[\]\)\}）】]\s*", re.I)
_BRACKET_DOT = re.compile(r"\s*[\[\(\{（【]\s*dot\s*[\]\)\}）】]\s*", re.I)
_WORD_AT = re.compile(r"\s+at\s+", re.I)
_WORD_DOT = re.compile(r"\s+dot\s+", re.I)


def is_valid_email(email: Optional[str]) -> bool:
    return bool(email and EMAIL_STRICT_RE.match(email))


def _unique(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen


def find_emails(text: str) -> list[str]:
    """Emails in text (lowercased, unique, in order). De-obfuscates [at]/(at)/ AT only if none is found directly."""
    found = _unique(m.lower() for m in EMAIL_FIND_RE.findall(text))
    if found:
        return found
    t = _BRACKET_AT.sub("@", text)
    t = _BRACKET_DOT.sub(".", t)
    t = _WORD_AT.sub("@", t)
    t = _WORD_DOT.sub(".", t)
    return _unique(m.lower() for m in EMAIL_FIND_RE.findall(t))


def email_domain_ok(email: str, allowed_domains: list[str]) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return any(domain == d.lower() or domain.endswith("." + d.lower()) for d in allowed_domains)


def email_on_page(email: str, page_text: str) -> bool:
    """Grounding check: the email must appear in the page text (after de-obfuscation)."""
    return email.lower() in find_emails(page_text)
