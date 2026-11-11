"""Per-client origin check for the public API.

The browser sends Origin (or Referer) on cross-site requests. A client's allowed_domains
lists the hosts that may call its widget API. Patterns are host or *.host, with an optional
:port. A pattern without a port matches any port; one with a port matches only that port.

This stops other sites from using a client's assistant from their own pages. It does not
authenticate anyone: a non-browser caller can set any header it likes.
"""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.db.models.client import ClientRecord

_DEV_HOSTS = frozenset({"localhost", "127.0.0.1"})

ORIGIN_FORBIDDEN_MESSAGE = "This assistant is not available from this site."
NO_ORIGIN_MESSAGE = "Requests must come from an allowed site."
NOT_CONFIGURED_MESSAGE = "This assistant has no allowed sites configured."


def _netloc_of(raw_url: str) -> tuple[str, str] | None:
    """(host:port, hostname) from an Origin or Referer value, lowercased; None if unusable."""
    try:
        parts = urlsplit(raw_url.strip())
        netloc = parts.netloc.rsplit("@", 1)[-1].lower()
        hostname = (parts.hostname or "").lower()
    except ValueError:
        return None
    if not netloc or not hostname:
        return None
    return netloc, hostname


def extract_origin(request: Request) -> tuple[str, str] | None:
    """Host from Origin, else Referer. Origin 'null' (sandboxed frames) falls through."""
    for header in ("origin", "referer"):
        raw = request.headers.get(header)
        if raw:
            found = _netloc_of(raw)
            if found:
                return found
    return None


def _pattern_matches(pattern: str, netloc: str, hostname: str) -> bool:
    target = netloc if ":" in pattern else hostname
    if pattern.startswith("*."):
        # *.example.com matches a subdomain, never the bare example.com.
        return target.endswith(pattern[1:])
    return target == pattern


def is_origin_allowed(origin: tuple[str, str], allowed_domains: list[str]) -> bool:
    netloc, hostname = origin
    if get_settings().app_env == "development" and hostname in _DEV_HOSTS:
        return True
    return any(
        _pattern_matches(pattern.strip().lower(), netloc, hostname)
        for pattern in allowed_domains
        if pattern.strip()
    )


def verify_client_origin(request: Request, client: ClientRecord) -> None:
    """Raise 403 unless the request's Origin or Referer is on the client's allowlist.

    With no patterns configured, development lets everything through and production refuses.
    """
    if not client.allowed_domains:
        if get_settings().app_env == "development":
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_CONFIGURED_MESSAGE)

    origin = extract_origin(request)
    if origin is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NO_ORIGIN_MESSAGE)

    if not is_origin_allowed(origin, client.allowed_domains):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ORIGIN_FORBIDDEN_MESSAGE)
