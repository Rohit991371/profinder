"""Grounding helpers (doc 02 rule 2): every stored value must be found in the fetched page text."""
import re
import unicodedata
from typing import Iterable, Optional

_SPLIT_KEYWORDS = re.compile(r"[，,；;、/\n]+")


def norm_text(s: Optional[str]) -> str:
    """NFKC + casefold + no whitespace (incl. U+3000). Used on BOTH sides of every grounding comparison."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).casefold()


def text_in(needle: Optional[str], page_text_norm: str) -> bool:
    n = norm_text(needle)
    return bool(n) and n in page_text_norm


def lines_grounded(lines: Iterable[str], page_text_norm: str) -> bool:
    ls = [ln for ln in lines if norm_text(ln)]
    return bool(ls) and all(norm_text(ln) in page_text_norm for ln in ls)


def join_interests(lines: list[str], max_chars: int = 800) -> Optional[str]:
    """Interest lines joined with '；', verbatim, capped at max_chars on a line boundary."""
    out: list[str] = []
    total = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if total + len(ln) > max_chars and out:
            break
        out.append(ln)
        total += len(ln) + 1
    return "；".join(out) or None


def keywords_from(text: Optional[str], limit: int = 10, max_len: int = 40) -> Optional[str]:
    """<= `limit` short phrases split out of the interests text (no new words are invented). None if nothing usable."""
    if not text or len(text) > 200 or "。" in text:
        return None                      # long prose: splitting it gives sentence fragments, not keywords
    seen: list[str] = []
    for part in _SPLIT_KEYWORDS.split(text):
        p = part.strip(" .。:：()（）")
        if 2 <= len(p) <= max_len and p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    return ", ".join(seen) or None
