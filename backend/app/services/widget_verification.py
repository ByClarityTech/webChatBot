"""Checks that the embed snippet is on a client's website (FR-12), without opening the server to
internal networks.

The admin gives a page URL. The check is refused unless all of these hold:

- The URL is https, has a host, has no user name or password, and uses port 443 or none.
- The host matches one of the client's allowed websites (the same rule as the chat API).
- The host resolves to public addresses only. Every address it resolves to is checked, and the
  first public one is used. The request connects to that address directly, so a second DNS
  answer cannot point it elsewhere (DNS rebinding).
- Redirects are not followed by the HTTP client. One redirect is allowed, and only to the same
  host. The new address is resolved and checked again.
- The response is read up to 256 KB, with a 5-second timeout.

The page HTML is searched for an iframe whose src is this installation's embed URL for the
client. A snippet added by JavaScript after load is not seen. The result says so.
"""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from app.core.origin_check import is_origin_allowed

CHECKS_PER_HOUR = 10
MAX_BODY_BYTES = 256 * 1024
TIMEOUT_SECONDS = 5.0
MAX_REDIRECTS = 1
MAX_URL_CHARS = 2048

OK = "OK"
INVALID_URL = "INVALID_URL"
NOT_ALLOWED_HOST = "NOT_ALLOWED_HOST"
BLOCKED_ADDRESS = "BLOCKED_ADDRESS"
FETCH_FAILED = "FETCH_FAILED"
REDIRECT_BLOCKED = "REDIRECT_BLOCKED"
TOO_LARGE = "TOO_LARGE"
SNIPPET_NOT_FOUND = "SNIPPET_NOT_FOUND"
WRONG_SOURCE = "WRONG_SOURCE"

MESSAGES = {
    OK: "The embed snippet is on this page.",
    INVALID_URL: "Enter a full https:// address for a page on your website.",
    NOT_ALLOWED_HOST: (
        "This site is not in your allowed websites. Add it under Allowed websites first."
    ),
    BLOCKED_ADDRESS: "This address is not a public website, so it cannot be checked.",
    FETCH_FAILED: "The page could not be loaded. Check the address and try again.",
    REDIRECT_BLOCKED: (
        "The page redirects somewhere this check does not follow. Enter the final address."
    ),
    TOO_LARGE: "The page is larger than the check reads (256 KB).",
    SNIPPET_NOT_FOUND: "No embed snippet was found in the page HTML.",
    WRONG_SOURCE: "The page has an iframe, but it does not point to this client's chat.",
}

NOTE = (
    "This check reads the HTML the server receives. A snippet that a script adds after the page "
    "loads is not seen. If the page is built that way, confirm the chat on the page yourself."
)

_IFRAME_SRC = re.compile(r"<iframe\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


class CheckRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    path: str
    url: str


@dataclass(frozen=True)
class VerifyResult:
    code: str
    message: str
    checked_url: str
    note: str = NOTE


def parse_target(raw_url: str) -> Target:
    raw = (raw_url or "").strip()
    if not raw or len(raw) > MAX_URL_CHARS:
        raise CheckRefused(INVALID_URL)
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise CheckRefused(INVALID_URL) from exc
    if parts.scheme != "https" or not parts.hostname:
        raise CheckRefused(INVALID_URL)
    if parts.username is not None or parts.password is not None:
        raise CheckRefused(INVALID_URL)
    if port not in (None, 443):
        raise CheckRefused(INVALID_URL)
    path = parts.path or "/"
    return Target(scheme="https", host=parts.hostname.lower(), path=path, url=raw)


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public_address(host: str) -> str:
    """Every address the host resolves to must be public. Returns the one to connect to."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CheckRefused(FETCH_FAILED) from exc
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise CheckRefused(BLOCKED_ADDRESS)
    return addresses[0]


def _connect_url(address: str, path: str) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"https://{host}{path}"


async def _fetch(host: str, address: str, path: str, transport) -> tuple[int, str | None, bytes]:
    """One request, pinned to `address`. TLS still checks the certificate for `host`."""
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=TIMEOUT_SECONDS,
        trust_env=False,
        transport=transport,
    ) as client:
        async with client.stream(
            "GET",
            _connect_url(address, path),
            headers={"Host": host, "Accept": "text/html", "User-Agent": "WidgetInstallCheck/1"},
            extensions={"sni_hostname": host},
        ) as response:
            location = response.headers.get("location")
            if 300 <= response.status_code < 400:
                return response.status_code, location, b""
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise CheckRefused(TOO_LARGE)
            return response.status_code, None, bytes(body)


def find_snippet(html: str, client_slug: str, public_url: str) -> str:
    """OK when the page has this client's embed iframe, pointing at this installation."""
    expected = urlsplit(public_url)
    expected_origin = (expected.scheme, expected.netloc.lower())
    candidates = []
    for raw_src in _IFRAME_SRC.findall(html):
        src = unescape(raw_src)
        parts = urlsplit(src)
        if "/embed" in parts.path:
            candidates.append(parts)
    if not candidates:
        return SNIPPET_NOT_FOUND
    for parts in candidates:
        if (parts.scheme, parts.netloc.lower()) != expected_origin:
            continue
        if parts.path.rstrip("/") == "/embed" and parse_qs(parts.query).get("client") == [
            client_slug
        ]:
            return OK
    return WRONG_SOURCE


def _result(code: str, checked_url: str) -> VerifyResult:
    return VerifyResult(code=code, message=MESSAGES[code], checked_url=checked_url)


async def verify_installation(
    *,
    client_slug: str,
    allowed_domains: list[str],
    raw_url: str,
    public_url: str,
    transport=None,
    resolver=resolve_public_address,
) -> VerifyResult:
    """Runs the check. Never raises for a bad target: the result code says what happened."""
    try:
        target = parse_target(raw_url)
        netloc = urlsplit(target.url).netloc.rsplit("@", 1)[-1].lower()
        if not is_origin_allowed((netloc, target.host), allowed_domains):
            raise CheckRefused(NOT_ALLOWED_HOST)

        address = await resolver(target.host)
        path = target.path
        redirects = 0
        while True:
            try:
                status_code, location, body = await _fetch(target.host, address, path, transport)
            except httpx.HTTPError as exc:
                raise CheckRefused(FETCH_FAILED) from exc
            if 300 <= status_code < 400:
                if redirects >= MAX_REDIRECTS or not location:
                    raise CheckRefused(REDIRECT_BLOCKED)
                next_url = urljoin(f"https://{target.host}{path}", location)
                try:
                    next_target = parse_target(next_url)
                except CheckRefused as exc:
                    raise CheckRefused(REDIRECT_BLOCKED) from exc
                if next_target.host != target.host:
                    raise CheckRefused(REDIRECT_BLOCKED)
                address = await resolver(target.host)
                path = next_target.path
                redirects += 1
                continue
            if status_code != 200:
                raise CheckRefused(FETCH_FAILED)
            break

        code = find_snippet(body.decode("utf-8", errors="replace"), client_slug, public_url)
        return _result(code, target.url)
    except CheckRefused as exc:
        return _result(exc.code, raw_url.strip())


def next_allowed_check_at(checks: list[datetime], now: datetime) -> datetime | None:
    """When the next check is allowed, or None if one is allowed now."""
    recent = sorted(c for c in checks if c > now - timedelta(hours=1))
    if len(recent) < CHECKS_PER_HOUR:
        return None
    return recent[len(recent) - CHECKS_PER_HOUR] + timedelta(hours=1)
