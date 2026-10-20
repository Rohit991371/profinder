"""Logging that cannot print secrets: env secret values, URL passwords, bearer tokens and Groq keys are masked."""
import logging
import os
import re
from typing import Iterable, Optional

from .settings import SECRET_ENV_NAMES

_URL_CREDS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s:@]+(?::[^/\s@]*)?@")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
_GROQ_KEY = re.compile(r"\bgsk_[A-Za-z0-9]{8,}")
MIN_SECRET_LEN = 4


def secret_values(names: Iterable[str] = SECRET_ENV_NAMES) -> list[str]:
    vals = [os.environ.get(n, "") for n in names]
    return sorted({v for v in vals if len(v) >= MIN_SECRET_LEN}, key=len, reverse=True)


def redact(text: str, secrets: Optional[Iterable[str]] = None) -> str:
    """Mask secrets in free text. `secrets` defaults to the current values of SECRET_ENV_NAMES."""
    values = list(secrets) if secrets is not None else secret_values()
    for v in sorted((v for v in values if len(v) >= MIN_SECRET_LEN), key=len, reverse=True):
        text = text.replace(v, "***")
    text = _URL_CREDS.sub(r"\g<scheme>***@", text)
    text = _BEARER.sub("Bearer ***", text)
    return _GROQ_KEY.sub("gsk_***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
