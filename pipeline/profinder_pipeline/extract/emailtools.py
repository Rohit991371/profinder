"""Email finding that also copes with spaces around '@' (seen on the ECNU list: 'mlan @ cs.ecnu.edu.cn').
The strict helpers in utils/emails.py are unchanged; the extractor and the grounding check both use these."""
import re
from typing import Iterable

from ..utils.emails import find_emails, is_valid_email

_SPACED_AT = re.compile(r"\s*@\s*")


def find_emails_loose(text: str) -> list[str]:
    """Emails in text, lowercased, de-obfuscated, unique, in order of appearance."""
    if not text:
        return []
    return find_emails(_SPACED_AT.sub("@", text))


def email_grounded(email: str, page_text: str) -> bool:
    """True if the email occurs on the page (after the same de-obfuscation used to find it)."""
    return bool(email) and email.lower() in find_emails_loose(page_text)


def valid_unique(emails: Iterable[str]) -> list[str]:
    out: list[str] = []
    for e in emails:
        e = (e or "").strip().lower()
        if e and is_valid_email(e) and e not in out:
            out.append(e)
    return out
