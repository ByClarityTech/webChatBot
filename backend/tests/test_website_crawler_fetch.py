"""SSRF protections for the crawler's fetcher: private addresses, cross-host redirects, DNS
rebinding, and the size and redirect limits.

The fetcher is the real one. Only the DNS answers and the HTTP transport are fakes.
"""

import httpx
import pytest
from crawler_fakes import PUBLIC_ADDRESS, SITE_HOST, FakeSite, html_page

from app.services import widget_verification as verify
from app.services.website_crawler.fetcher import (
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    OFF_SITE,
    fetch_public,
)
from app.services.widget_verification import CheckRefused

PUBLIC_URL = f"https://{SITE_HOST}/hours"


def refused_code(exc_info) -> str:
    return exc_info.value.code


def stub_resolver(answers: dict[str, list[str]], calls: list[str]):
    """Answers per host, one per call; the last answer repeats."""

    async def resolve(host: str) -> str:
        calls.append(host)
        queue = answers[host]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return resolve


# ---------------------------------------------------------------- addresses


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:10.0.0.5",  # IPv4-mapped private
    ],
)
def test_private_and_special_addresses_are_not_public(address):
    assert verify.is_public_address(address) is False


def test_a_public_address_is_public():
    assert verify.is_public_address(PUBLIC_ADDRESS) is True


async def test_a_host_that_resolves_to_a_private_address_is_refused(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        return [(2, 1, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(verify.CheckRefused) as exc:
        await verify.resolve_public_address(SITE_HOST)
    assert refused_code(exc) == verify.BLOCKED_ADDRESS


async def test_one_private_answer_among_public_ones_is_refused(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        return [(2, 1, 6, "", (PUBLIC_ADDRESS, 443)), (2, 1, 6, "", ("192.168.0.9", 443))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(verify.CheckRefused) as exc:
        await verify.resolve_public_address(SITE_HOST)
    assert refused_code(exc) == verify.BLOCKED_ADDRESS


async def test_a_private_host_is_never_contacted():
    site = FakeSite({"/hours": html_page("Hours", "Open.")})

    async def private_resolver(host: str) -> str:
        return "10.0.0.5"

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=private_resolver, transport=site.transport()
        )
    assert refused_code(exc) == verify.BLOCKED_ADDRESS
    assert site.requests == []


# ---------------------------------------------------------------- pinned connection


async def test_the_request_goes_to_the_resolved_address_with_the_host_name_kept():
    site = FakeSite({"/hours": html_page("Hours", "Open.")})
    calls: list[str] = []
    resolver = stub_resolver({SITE_HOST: [PUBLIC_ADDRESS]}, calls)

    result = await fetch_public(
        PUBLIC_URL, site_host=SITE_HOST, resolver=resolver, transport=site.transport()
    )

    assert result.status_code == 200
    host_header, request_url = site.requests[-1]
    assert host_header == SITE_HOST
    assert request_url.startswith(f"https://{PUBLIC_ADDRESS}/hours")


async def test_dns_rebinding_cannot_move_the_connection():
    """The second answer for the host is private. The request must still go to the address
    that was checked first, and the host is resolved once for the hop."""
    site = FakeSite({"/hours": html_page("Hours", "Open.")})
    calls: list[str] = []
    resolver = stub_resolver({SITE_HOST: [PUBLIC_ADDRESS, "10.0.0.1"]}, calls)

    await fetch_public(
        PUBLIC_URL, site_host=SITE_HOST, resolver=resolver, transport=site.transport()
    )

    assert calls == [SITE_HOST]
    assert all(PUBLIC_ADDRESS in url for _, url in site.requests)
    assert not any("10.0.0.1" in url for _, url in site.requests)


async def test_a_second_answer_is_checked_when_the_request_is_repeated():
    """Each new request resolves the host again and must be public again."""
    site = FakeSite({"/hours": html_page("Hours", "Open.")})

    async def turns_private(host: str) -> str:
        return PUBLIC_ADDRESS if not site.requests else "10.0.0.1"

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=turns_private, transport=site.transport()
        )
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=turns_private, transport=site.transport()
        )
    assert refused_code(exc) == verify.BLOCKED_ADDRESS


# ---------------------------------------------------------------- redirects


async def test_a_redirect_to_another_host_is_refused_and_not_resolved():
    site = FakeSite({"/hours": (302, "", {"location": "https://evil.example/steal"})})
    calls: list[str] = []
    resolver = stub_resolver({SITE_HOST: [PUBLIC_ADDRESS]}, calls)

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=resolver, transport=site.transport()
        )
    assert refused_code(exc) == verify.REDIRECT_BLOCKED
    assert calls == [SITE_HOST]
    assert not any("evil.example" in url for _, url in site.requests)


async def test_a_redirect_to_the_www_counterpart_is_followed():
    site = FakeSite(
        {
            "/hours": (301, "", {"location": f"https://www.{SITE_HOST}/opening"}),
            "/opening": html_page("Opening hours", "Open."),
        }
    )
    calls: list[str] = []
    resolver = stub_resolver(
        {SITE_HOST: [PUBLIC_ADDRESS], f"www.{SITE_HOST}": [PUBLIC_ADDRESS]}, calls
    )

    result = await fetch_public(
        PUBLIC_URL, site_host=SITE_HOST, resolver=resolver, transport=site.transport()
    )

    assert result.final_url == f"https://www.{SITE_HOST}/opening"
    assert calls == [SITE_HOST, f"www.{SITE_HOST}"]


async def test_each_redirect_hop_is_resolved_and_checked_again():
    site = FakeSite(
        {
            "/hours": (302, "", {"location": "/opening"}),
            "/opening": html_page("Opening hours", "Open."),
        }
    )
    calls: list[str] = []
    second_public = "93.184.216.35"
    resolver = stub_resolver({SITE_HOST: [PUBLIC_ADDRESS, second_public]}, calls)

    result = await fetch_public(
        PUBLIC_URL, site_host=SITE_HOST, resolver=resolver, transport=site.transport()
    )

    assert result.final_url == f"https://{SITE_HOST}/opening"
    assert calls == [SITE_HOST, SITE_HOST]
    assert site.requests[0][1].startswith(f"https://{PUBLIC_ADDRESS}/")
    assert site.requests[1][1].startswith(f"https://{second_public}/opening")


async def test_too_many_redirects_are_refused():
    chain = {f"/r{i}": (302, "", {"location": f"/r{i + 1}"}) for i in range(MAX_REDIRECTS + 2)}
    site = FakeSite(chain)

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            f"https://{SITE_HOST}/r0",
            site_host=SITE_HOST,
            resolver=public,
            transport=site.transport(),
        )
    assert refused_code(exc) == verify.REDIRECT_BLOCKED


async def test_a_redirect_to_plain_http_is_refused():
    site = FakeSite({"/hours": (302, "", {"location": f"http://{SITE_HOST}/hours"})})

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=public, transport=site.transport()
        )
    assert refused_code(exc) == verify.REDIRECT_BLOCKED


async def test_a_redirect_with_no_location_is_refused():
    site = FakeSite({"/hours": (302, "", {})})

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=public, transport=site.transport()
        )
    assert refused_code(exc) == verify.REDIRECT_BLOCKED


# ---------------------------------------------------------------- limits and failures


async def test_a_body_over_the_limit_is_refused():
    site = FakeSite({"/hours": (200, b"x" * (MAX_PAGE_BYTES + 1), {"content-type": "text/html"})})

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL, site_host=SITE_HOST, resolver=public, transport=site.transport()
        )
    assert refused_code(exc) == verify.TOO_LARGE


async def test_an_error_status_is_returned_for_the_caller_to_judge():
    site = FakeSite({"/hours": (503, "down", {"content-type": "text/plain"})})

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    result = await fetch_public(
        PUBLIC_URL, site_host=SITE_HOST, resolver=public, transport=site.transport()
    )
    assert result.status_code == 503


async def test_a_connection_error_is_a_fetch_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(verify.CheckRefused) as exc:
        await fetch_public(
            PUBLIC_URL,
            site_host=SITE_HOST,
            resolver=public,
            transport=httpx.MockTransport(handler),
        )
    assert refused_code(exc) == verify.FETCH_FAILED


async def test_a_url_off_the_site_is_not_fetched_at_all():
    site = FakeSite()

    async def public(host: str) -> str:
        return PUBLIC_ADDRESS

    with pytest.raises(CheckRefused) as exc:
        await fetch_public(
            "https://evil.example/",
            site_host=SITE_HOST,
            resolver=public,
            transport=site.transport(),
        )
    assert refused_code(exc) == OFF_SITE
    assert site.requests == []
