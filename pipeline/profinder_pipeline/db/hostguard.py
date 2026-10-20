"""Host safety rules (doc 03 section 0). Only host names are ever shown, never a full URL."""
import os
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class HostRefused(RuntimeError):
    pass


def url_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def host_label(url: str) -> str:
    """'host:port' for logs. Never includes user, password, database name or query."""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    return f"{host}:{parts.port}" if parts.port else host


def is_local(url: str) -> bool:
    return url_host(url) in LOCAL_HOSTS


def require_local(url: str, what: str) -> str:
    """For throwaway-DB operations (app-schema, seed-snapshot): refuse anything that is not localhost."""
    if not is_local(url):
        raise HostRefused(f"{what}: refusing host '{url_host(url)}'. This command only runs against the local Docker database.")
    return host_label(url)


def refuse_production(url: str, what: str, allow_env: str) -> str:
    """Refuse hosts whose name contains prod/production unless the allow variable is set to '1' (owner only)."""
    host = url_host(url)
    if ("prod" in host) and os.environ.get(allow_env) != "1":
        raise HostRefused(f"{what}: host '{host}' looks like production. Set {allow_env}=1 only if you are the owner and mean it.")
    return host_label(url)
