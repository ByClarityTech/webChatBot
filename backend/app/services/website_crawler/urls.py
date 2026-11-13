"""Which links the crawler follows, and the one form each page is stored under.

A page is stored by its canonical URL: https, lowercase host, no fragment, no query string,
no trailing slash (except the site root). Only the site's exact host and its www. counterpart
are in scope.
"""

import re
from urllib.parse import urljoin, urlsplit

MAX_PAGES = 30
MAX_DEPTH = 3
MAX_URL_CHARS = 2048

# Links to these are not pages. The crawler never fetches them.
_FILE_EXTENSIONS = frozenset(
    {
        ".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
        ".mp3", ".mp4", ".mov", ".avi", ".wav",
        ".css", ".js", ".json", ".xml", ".txt",
    }
)  # fmt: skip


def site_root(host: str) -> str:
    return f"https://{host}/"


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def same_site(candidate_host: str, site_host: str) -> bool:
    """The site's exact host, or its www. counterpart (clinic.com and www.clinic.com)."""
    candidate = candidate_host.lower()
    site = site_host.lower()
    if candidate == site:
        return True
    return candidate == f"www.{site}" or site == f"www.{candidate}"


def canonical_url(raw: str, base: str | None = None) -> str | None:
    """The canonical form of a link, or None when the crawler must not follow it.

    Relative links are resolved against base. Refused: anything not https, a user name or
    password, a port other than 443, an over-long URL, and file links (see _FILE_EXTENSIONS).
    """
    href = (raw or "").strip()
    if not href or len(href) > MAX_URL_CHARS:
        return None
    absolute = urljoin(base, href) if base else href
    try:
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 443):
        return None

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = path.rstrip("/") or "/"
    last_segment = path.rsplit("/", 1)[-1].lower()
    if "." in last_segment and "." + last_segment.rsplit(".", 1)[-1] in _FILE_EXTENSIONS:
        return None
    return f"https://{parts.hostname.lower()}{path}"


def in_scope(url: str, site_host: str) -> bool:
    """True when the URL's host is the site's host or its www. counterpart."""
    host = host_of(url)
    return bool(host) and same_site(host, site_host)
