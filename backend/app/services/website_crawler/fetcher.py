"""SSRF-safe GET for the crawler: the widget check's rules, applied to every request.

For each request the host is resolved, every address must be public, and the request connects
to the first one. The host name is kept for TLS (SNI and the certificate check) and the Host
header, so a second DNS answer cannot send the connection somewhere else.

Redirects are followed by hand. Each hop must stay on the site (the host or its www. counterpart),
and each hop is resolved and checked again. Redirects are capped. The body is read up to a limit.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.website_crawler.urls import canonical_url, host_of, in_scope
from app.services.widget_verification import (
    BLOCKED_ADDRESS,
    FETCH_FAILED,
    REDIRECT_BLOCKED,
    TOO_LARGE,
    CheckRefused,
    is_public_address,
    resolve_public_address,
)

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 10.0
ACCEPT = "text/html, application/xhtml+xml, text/plain;q=0.9, */*;q=0.1"
USER_AGENT = "WebsiteAssistantCrawler/1.0 (+site content index)"
# A URL that is not on the site (a link or a request), as opposed to a redirect off the site.
OFF_SITE = "OFF_SITE"

# Resolves a host name to the one public address to connect to, or raises CheckRefused.
Resolver = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    content_type: str
    body: bytes
    final_url: str


def _pinned_url(address: str, path: str) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"https://{host}{path}"


async def _get_once(
    host: str, address: str, path: str, transport: httpx.AsyncBaseTransport | None
) -> tuple[int, str | None, str, bytes]:
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=TIMEOUT_SECONDS,
            trust_env=False,
            transport=transport,
        ) as client:
            async with client.stream(
                "GET",
                _pinned_url(address, path),
                headers={"Host": host, "Accept": ACCEPT, "User-Agent": USER_AGENT},
                extensions={"sni_hostname": host},
            ) as response:
                location = response.headers.get("location")
                content_type = response.headers.get("content-type", "")
                if 300 <= response.status_code < 400:
                    return response.status_code, location, content_type, b""
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PAGE_BYTES:
                        raise CheckRefused(TOO_LARGE)
                return response.status_code, None, content_type, bytes(body)
    except httpx.HTTPError as exc:
        raise CheckRefused(FETCH_FAILED) from exc


async def fetch_public(
    url: str,
    *,
    site_host: str,
    resolver: Resolver = resolve_public_address,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """GET an https URL on the site. Raises CheckRefused with a code when the URL is refused.

    A non-2xx answer is returned as is (the caller decides what a 404 or a 500 means). A
    redirect off the site, a loop, a private address, or an over-long body raises.
    """
    current = url
    redirects = 0
    while True:
        if not in_scope(current, site_host):
            raise CheckRefused(OFF_SITE)
        host = host_of(current)
        path = urlsplit(current).path or "/"
        address = await resolver(host)
        # The resolver already checks every answer; this holds the line if it ever does not.
        if not is_public_address(address):
            raise CheckRefused(BLOCKED_ADDRESS)
        status, location, content_type, body = await _get_once(host, address, path, transport)
        if 300 <= status < 400:
            if redirects >= MAX_REDIRECTS or not location:
                raise CheckRefused(REDIRECT_BLOCKED)
            next_url = canonical_url(urljoin(current, location))
            if next_url is None or not in_scope(next_url, site_host):
                raise CheckRefused(REDIRECT_BLOCKED)
            current = next_url
            redirects += 1
            continue
        return FetchResult(
            status_code=status, content_type=content_type, body=body, final_url=current
        )
