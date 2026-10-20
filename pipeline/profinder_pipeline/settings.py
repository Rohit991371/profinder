"""Settings from environment variables (pipeline/.env is loaded if present). Secrets are never printed."""
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from .paths import PIPELINE_DIR

# Variable names whose VALUES must never appear in logs, errors or chat.
SECRET_ENV_NAMES = (
    "GROQ_API_KEY", "OPENALEX_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY",
    "PG_PASSWORD", "PROXY_URL", "PIPELINE_DATABASE_URL", "APP_DATABASE_URL",
)
# (name, required?)  required = doctor reports FAIL when missing.
EXPECTED_ENV = (
    ("PIPELINE_DATABASE_URL", True), ("APP_DATABASE_URL", True),
    ("GROQ_API_KEY", False), ("GROQ_MODEL", False), ("OPENALEX_API_KEY", False),
    ("SERPER_API_KEY", False), ("TAVILY_API_KEY", False), ("PROXY_URL", False),
)


def load_env() -> None:
    """Load pipeline/.env without overriding variables already set in the shell."""
    load_dotenv(PIPELINE_DIR / ".env", override=False)


def _get(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value if value else None


@dataclass(frozen=True)
class Settings:
    # repr=False everywhere: a stray print(settings) must not leak anything.
    pipeline_database_url: Optional[str] = field(default=None, repr=False)
    app_database_url: Optional[str] = field(default=None, repr=False)
    groq_api_key: Optional[str] = field(default=None, repr=False)
    groq_model: Optional[str] = field(default=None, repr=False)
    openalex_api_key: Optional[str] = field(default=None, repr=False)
    serper_api_key: Optional[str] = field(default=None, repr=False)
    tavily_api_key: Optional[str] = field(default=None, repr=False)
    proxy_url: Optional[str] = field(default=None, repr=False)


def get_settings() -> Settings:
    load_env()
    return Settings(
        pipeline_database_url=_get("PIPELINE_DATABASE_URL"), app_database_url=_get("APP_DATABASE_URL"),
        groq_api_key=_get("GROQ_API_KEY"), groq_model=_get("GROQ_MODEL"),
        openalex_api_key=_get("OPENALEX_API_KEY"), serper_api_key=_get("SERPER_API_KEY"),
        tavily_api_key=_get("TAVILY_API_KEY"), proxy_url=_get("PROXY_URL"),
    )


def env_presence() -> list[tuple[str, bool, bool]]:
    """[(name, is_set, required)] - names only, never values."""
    load_env()
    return [(name, bool(os.environ.get(name)), required) for name, required in EXPECTED_ENV]
