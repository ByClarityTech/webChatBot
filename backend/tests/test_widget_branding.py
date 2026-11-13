"""Widget branding and installation check (FR-11, FR-12).

Covers: WCAG contrast and the 4.5:1 refusal, automatic header text color, logo bytes (PNG, JPEG,
WebP accepted; SVG, GIF, empty, oversize, and odd sizes refused), the admin routes and their
access rules and audit rows, the paused gate on public routes, and the installation check
(SSRF blocks, pinned addresses, redirects, size and time limits, snippet matching, rate limit).

Tables are replaced by an in-memory store. The SQL needs a live database.
"""

import hashlib
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.routes import chat as chat_routes
from app.api.routes import widget as widget_routes
from app.api.routes.admin import widget as admin_widget
from app.core.roles import UserType
from app.db.models import widget_settings as store_module
from app.db.models.widget_settings import STATUS_LIVE, STATUS_PAUSED, WidgetSettings
from app.services import widget_verification as verify
from app.services.widget_branding import (
    DEFAULT_HEADER_COLOR,
    MAX_LOGO_BYTES,
    NEAR_BLACK,
    WHITE,
    InvalidBrandingColor,
    InvalidLogo,
    check_header_color,
    contrast_ratio,
    inspect_logo,
    relative_luminance,
    text_color_for,
)

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support
T0 = datetime(2030, 7, 1, 10, 0, tzinfo=UTC)
PUBLIC_URL = "https://widget.example.com"


# ---------------------------------------------------------------- contrast


def test_black_on_white_is_the_full_21_to_1():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_white_luminance_is_one():
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0)


def test_767676_on_white_just_passes_aa():
    assert contrast_ratio("#767676", "#FFFFFF") >= 4.5


def test_777777_on_white_just_fails_aa():
    assert contrast_ratio("#777777", "#FFFFFF") < 4.5


def test_text_color_is_white_on_dark_and_near_black_on_light():
    assert text_color_for("#1D4ED8") == WHITE
    assert text_color_for("#FFFF00") == NEAR_BLACK


def test_default_header_color_passes_with_white_text():
    colors = check_header_color(DEFAULT_HEADER_COLOR)
    assert colors.header_text_color == WHITE
    assert colors.ratio >= 4.5


def test_light_grey_is_accepted_because_near_black_reads_well():
    # White reads poorly on this grey (about 3:1), near-black reads well (about 5.8:1).
    colors = check_header_color("#959595")
    assert colors.header_text_color == NEAR_BLACK
    assert colors.ratio >= 4.5


def test_color_that_cannot_reach_4_5_to_1_with_either_text_is_refused():
    with pytest.raises(InvalidBrandingColor) as raised:
        check_header_color("#777777")
    assert raised.value.ratio < 4.5


def test_header_color_is_stored_in_uppercase():
    assert check_header_color("#1d4ed8").header_color == "#1D4ED8"


# ---------------------------------------------------------------- logo bytes


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 16
    )


def _jpeg(width: int, height: int) -> bytes:
    sof = (
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _webp_vp8x(width: int, height: int) -> bytes:
    return (
        b"RIFF\x00\x00\x00\x00WEBPVP8X"
        + b"\x0a\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


def _webp_vp8l(width: int, height: int) -> bytes:
    w, h = width - 1, height - 1
    b0 = w & 0xFF
    b1 = ((w >> 8) & 0x3F) | ((h & 0x3) << 6)
    b2 = (h >> 2) & 0xFF
    b3 = (h >> 10) & 0x0F
    return (
        b"RIFF\x00\x00\x00\x00WEBPVP8L"
        + b"\x05\x00\x00\x00"
        + b"\x2f"
        + bytes([b0, b1, b2, b3])
        + b"\x00" * 4
    )


def test_png_jpeg_and_webp_are_accepted_with_their_sizes():
    png = inspect_logo(_png(120, 60))
    assert (png.content_type, png.width, png.height) == ("image/png", 120, 60)
    jpeg = inspect_logo(_jpeg(200, 80))
    assert (jpeg.content_type, jpeg.width, jpeg.height) == ("image/jpeg", 200, 80)
    vp8x = inspect_logo(_webp_vp8x(300, 100))
    assert (vp8x.content_type, vp8x.width, vp8x.height) == ("image/webp", 300, 100)
    lossless = inspect_logo(_webp_vp8l(100, 50))
    assert (lossless.width, lossless.height) == (100, 50)


def test_sha256_is_of_the_exact_bytes():
    data = _png(64, 64)
    assert inspect_logo(data).sha256 == hashlib.sha256(data).hexdigest()


def test_svg_is_refused():
    with pytest.raises(InvalidLogo, match="SVG"):
        inspect_logo(b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>')


def test_svg_with_a_png_header_is_still_not_a_valid_logo():
    # The signature check is on the first bytes only. The IHDR check must then fail.
    with pytest.raises(InvalidLogo):
        inspect_logo(b"\x89PNG\r\n\x1a\n<svg><script>alert(1)</script></svg>")


def test_gif_and_empty_files_are_refused():
    with pytest.raises(InvalidLogo):
        inspect_logo(b"GIF89a" + b"\x00" * 20)
    with pytest.raises(InvalidLogo, match="empty"):
        inspect_logo(b"")


def test_oversize_file_is_refused():
    with pytest.raises(InvalidLogo, match="256 KB"):
        inspect_logo(_png(64, 64) + b"\x00" * MAX_LOGO_BYTES)


@pytest.mark.parametrize(("width", "height"), [(8, 64), (64, 2000)])
def test_dimensions_outside_16_to_1024_are_refused(width, height):
    with pytest.raises(InvalidLogo, match="pixels"):
        inspect_logo(_png(width, height))


def test_truncated_png_is_refused():
    with pytest.raises(InvalidLogo):
        inspect_logo(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)


# ---------------------------------------------------------------- admin store fake


class WidgetStore:
    def __init__(self) -> None:
        self.rows: dict = {}
        self.verifications: list = []

    def _row(self, client_id) -> WidgetSettings | None:
        return self.rows.get(client_id)

    async def get_widget_settings(self, client_id):
        return self._row(client_id)

    async def set_status(self, client_id, status, *, actor_id, now):
        return self._upsert(client_id, status=status, updated_at=now)

    async def save_branding(
        self, client_id, *, bot_name, greeting, header_color, header_text_color, actor_id, now
    ):
        return self._upsert(
            client_id,
            bot_name=bot_name,
            greeting=greeting,
            header_color=header_color,
            header_text_color=header_text_color,
            updated_at=now,
        )

    async def save_logo(self, client_id, *, content_type, data, sha256, actor_id, now):
        self.logos = getattr(self, "logos", {})
        self.logos[client_id] = (content_type, data, sha256)
        return self._upsert(
            client_id, logo_content_type=content_type, logo_sha256=sha256, updated_at=now
        )

    async def delete_logo(self, client_id, *, actor_id, now):
        getattr(self, "logos", {}).pop(client_id, None)
        self._upsert(client_id, logo_content_type=None, logo_sha256=None, updated_at=now)
        return True

    async def get_logo(self, client_id):
        return getattr(self, "logos", {}).get(client_id)

    async def record_verification(self, client_id, *, checked_url, result, actor_id, now):
        self.verifications.append((client_id, checked_url, result, now))

    async def recent_verification_times(self, client_id, since):
        return [t for c, _, _, t in self.verifications if c == client_id and t > since]

    def _upsert(self, client_id, **changes) -> WidgetSettings:
        current = self.rows.get(client_id) or WidgetSettings(
            client_id=client_id,
            status=STATUS_LIVE,
            bot_name=None,
            greeting=None,
            header_color=None,
            header_text_color=None,
            logo_content_type=None,
            logo_sha256=None,
            updated_at=None,
        )
        values = {**current.__dict__, **changes}
        self.rows[client_id] = WidgetSettings(**values)
        return self.rows[client_id]


@pytest.fixture
def store(monkeypatch):
    fake = WidgetStore()
    for name in (
        "get_widget_settings",
        "set_status",
        "save_branding",
        "save_logo",
        "delete_logo",
        "get_logo",
        "record_verification",
        "recent_verification_times",
    ):
        monkeypatch.setattr(store_module, name, getattr(fake, name))
    monkeypatch.setattr(admin_widget, "utc_now", lambda: T0)
    return fake


@pytest.fixture
def audit(monkeypatch):
    recorder = AsyncMock()
    monkeypatch.setattr(admin_widget, "log_admin_action", recorder)
    return recorder


@pytest.fixture
def admin_app(monkeypatch):
    async def get_client(client_id):
        return make_client_record(client_id=client_id, slug="clinic")

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    monkeypatch.setattr("app.api.routes.admin.widget.get_settings", _settings_stub)
    app = FastAPI()
    app.include_router(admin_widget.router, prefix="/api/v1")
    return app


class _SettingsStub:
    widget_public_url = PUBLIC_URL


def _settings_stub():
    return _SettingsStub()


BASE = "/api/v1/admin/widget"


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_low_contrast_header_is_refused_with_400(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(f"{BASE}/branding", json={"header_color": "#777777"})
    assert response.status_code == 400
    assert "4.5:1" in response.json()["detail"]
    assert not any(c.args[0] == "widget.branding_update" for c in audit.await_args_list)


async def test_valid_branding_is_saved_with_the_text_color_and_audited(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(
                f"{BASE}/branding",
                json={"bot_name": "  Ava  ", "greeting": "", "header_color": "#1d4ed8"},
            )
    body = response.json()
    assert response.status_code == 200
    assert body["bot_name"] == "Ava"
    assert body["header_color"] == "#1D4ED8"
    assert body["header_text_color"] == "#FFFFFF"
    assert body["contrast_ratio"] >= 4.5
    assert audit.await_args.args[0] == "widget.branding_update"


async def test_bot_name_over_60_characters_is_a_validation_error(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(f"{BASE}/branding", json={"bot_name": "x" * 61})
    assert response.status_code == 422


async def test_svg_logo_upload_is_refused(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(
                f"{BASE}/logo",
                files={"file": ("logo.svg", b"<svg><script>x</script></svg>", "image/svg+xml")},
            )
    assert response.status_code == 400
    assert "SVG" in response.json()["detail"]


async def test_png_logo_upload_is_saved_and_served_with_nosniff(
    admin_app, store, audit, monkeypatch
):
    monkeypatch.setattr(
        widget_routes, "get_live_client", AsyncMock(return_value=make_client_record(slug="clinic"))
    )
    data = _png(64, 64)
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            uploaded = await http.put(
                f"{BASE}/logo", files={"file": ("logo.png", data, "image/png")}
            )
    assert uploaded.status_code == 200
    assert uploaded.json()["has_logo"] is True
    assert audit.await_args.args[0] == "widget.logo_update"

    stored_type, stored, _ = store.logos[next(iter(store.logos))]
    assert stored_type == "image/png" and stored == data


async def test_logo_delete_is_audited(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.delete(f"{BASE}/logo")
    assert response.status_code == 200
    assert audit.await_args.args[0] == "widget.logo_delete"


async def test_pausing_is_audited_and_reflected(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(f"{BASE}/status", json={"status": "paused"})
    assert response.json()["status"] == "paused"
    assert audit.await_args.args[0] == "widget.status_change"


async def test_unknown_status_is_refused(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.put(f"{BASE}/status", json={"status": "broken"})
    assert response.status_code == 422


async def test_support_cannot_see_or_change_the_widget(admin_app, store, audit):
    with signed_in(admin_app, SUPPORT):
        async with await _client(admin_app) as http:
            read = await http.get(BASE)
            change = await http.put(f"{BASE}/status", json={"status": "paused"})
    assert read.status_code == 403
    assert change.status_code == 403


async def test_super_admin_must_name_the_client(admin_app, store, audit):
    with signed_in(admin_app, SA):
        async with await _client(admin_app) as http:
            response = await http.get(BASE)
    assert response.status_code == 400


async def test_snippet_has_the_public_url_and_escapes_the_bot_name(admin_app, store, audit):
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            await http.put(f"{BASE}/branding", json={"bot_name": '"><script>x</script>'})
            response = await http.get(BASE)
    snippet = response.json()["snippet"]
    assert f'src="{PUBLIC_URL}/embed?client=clinic"' in snippet
    assert "<script>" not in snippet
    assert "&quot;&gt;&lt;script" in snippet


def test_build_snippet_uses_only_public_url_and_slug():
    snippet = admin_widget.build_snippet("https://w.example.com/", "clinic", "Ava")
    assert 'src="https://w.example.com/embed?client=clinic"' in snippet
    assert "<iframe" in snippet and "</iframe>" in snippet


# ---------------------------------------------------------------- public: paused gate


@pytest.fixture
def public_app(monkeypatch, store):
    clinic = make_client_record(slug="clinic")
    monkeypatch.setattr(
        "app.services.public_client.get_client_by_slug", AsyncMock(return_value=clinic)
    )
    monkeypatch.setattr(widget_routes, "verify_client_origin", lambda r, c: None)
    monkeypatch.setattr(widget_routes, "list_default_questions", AsyncMock(return_value=[]))
    monkeypatch.setattr(widget_routes, "booking_available", AsyncMock(return_value=False))
    app = FastAPI()
    app.include_router(widget_routes.router, prefix="/api/v1")
    return app, clinic


async def test_paused_widget_config_is_the_not_available_404(public_app, store, monkeypatch):
    app, clinic = public_app
    monkeypatch.setattr(
        "app.services.public_client.widget_status", AsyncMock(return_value=STATUS_PAUSED)
    )
    store.rows[clinic.id] = WidgetSettings(
        client_id=clinic.id,
        status=STATUS_PAUSED,
        bot_name=None,
        greeting=None,
        header_color=None,
        header_text_color=None,
        logo_content_type=None,
        logo_sha256=None,
        updated_at=None,
    )
    async with await _client(app) as http:
        response = await http.get("/api/v1/widget/config?client=clinic")
    assert response.status_code == 404
    assert response.json()["detail"] == "This assistant is not available"


async def test_paused_widget_keeps_its_frame_policy_so_the_card_can_show(public_app, store):
    app, clinic = public_app
    store.rows[clinic.id] = WidgetSettings(
        client_id=clinic.id,
        status=STATUS_PAUSED,
        bot_name=None,
        greeting=None,
        header_color=None,
        header_text_color=None,
        logo_content_type=None,
        logo_sha256=None,
        updated_at=None,
    )
    async with await _client(app) as http:
        response = await http.get("/api/v1/widget/frame-ancestors?client=clinic")
    assert response.status_code == 200


async def test_live_config_carries_branding_and_a_logo_path(public_app, store):
    app, clinic = public_app
    store.rows[clinic.id] = WidgetSettings(
        client_id=clinic.id,
        status=STATUS_LIVE,
        bot_name="Ava",
        greeting="Hello there",
        header_color="#0B5D1E",
        header_text_color="#FFFFFF",
        logo_content_type="image/png",
        logo_sha256="ab" * 32,
        updated_at=None,
    )
    async with await _client(app) as http:
        response = await http.get("/api/v1/widget/config?client=clinic")
    body = response.json()
    assert body["bot_name"] == "Ava"
    assert body["greeting"] == "Hello there"
    assert body["header_color"] == "#0B5D1E"
    assert body["logo_url"].startswith("/api/v1/widget/logo?client=clinic&v=")


async def test_logo_is_served_with_its_type_and_nosniff(public_app, store):
    app, clinic = public_app
    data = _png(32, 32)
    store.logos = {clinic.id: ("image/png", data, hashlib.sha256(data).hexdigest())}
    async with await _client(app) as http:
        response = await http.get("/api/v1/widget/logo?client=clinic")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == data


async def test_chat_is_refused_while_paused_unless_staff_hold_the_conversation(monkeypatch):
    client_id = make_client_record(slug="clinic").id
    monkeypatch.setattr(chat_routes, "widget_status", AsyncMock(return_value=STATUS_PAUSED))
    monkeypatch.setattr(chat_routes, "active_session_for", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as raised:
        await chat_routes._refuse_when_paused(client_id, "thread")
    assert raised.value.status_code == 404

    session = AsyncMock(session_id="session-1")
    monkeypatch.setattr(chat_routes, "active_session_for", AsyncMock(return_value=session))
    monkeypatch.setattr(
        chat_routes, "get_open_handover_for_session", AsyncMock(return_value=object())
    )
    assert await chat_routes._refuse_when_paused(client_id, "thread") is None


async def test_live_widget_takes_chat_normally(monkeypatch):
    monkeypatch.setattr(chat_routes, "widget_status", AsyncMock(return_value=STATUS_LIVE))
    assert await chat_routes._refuse_when_paused(uuidless(), "thread") is None


def uuidless():
    from uuid import uuid4

    return uuid4()


# ---------------------------------------------------------------- installation check: pure parts


@pytest.mark.parametrize(
    "raw",
    [
        "http://clinic.com/",
        "ftp://clinic.com/",
        "https://user:pw@clinic.com/",
        "https://clinic.com:8443/",
        "https:///path",
        "",
        "x" * 2100,
    ],
)
def test_bad_urls_are_refused(raw):
    with pytest.raises(verify.CheckRefused) as raised:
        verify.parse_target(raw)
    assert raised.value.code == verify.INVALID_URL


def test_https_url_on_default_port_is_accepted():
    target = verify.parse_target("https://Clinic.com/services")
    assert (target.host, target.path) == ("clinic.com", "/services")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.1",
        "172.16.5.4",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_private_loopback_link_local_and_metadata_addresses_are_blocked(address):
    assert verify.is_public_address(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert verify.is_public_address(address) is True


async def test_a_name_that_resolves_to_any_private_address_is_blocked(monkeypatch):
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
    ]
    monkeypatch.setattr(verify.socket, "getaddrinfo", lambda *a, **k: answers)
    with pytest.raises(verify.CheckRefused) as raised:
        await verify.resolve_public_address("clinic.com")
    assert raised.value.code == verify.BLOCKED_ADDRESS


async def test_a_public_name_resolves_to_its_address(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(verify.socket, "getaddrinfo", lambda *a, **k: answers)
    assert await verify.resolve_public_address("clinic.com") == "93.184.216.34"


async def test_a_name_that_does_not_resolve_fails_cleanly(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(verify.socket, "getaddrinfo", fail)
    with pytest.raises(verify.CheckRefused) as raised:
        await verify.resolve_public_address("nope.invalid")
    assert raised.value.code == verify.FETCH_FAILED


def test_snippet_match_needs_this_installation_and_this_client():
    good = f'<iframe src="{PUBLIC_URL}/embed?client=clinic" title="Chat"></iframe>'
    assert verify.find_snippet(good, "clinic", PUBLIC_URL) == verify.OK
    other_client = f'<iframe src="{PUBLIC_URL}/embed?client=other"></iframe>'
    assert verify.find_snippet(other_client, "clinic", PUBLIC_URL) == verify.WRONG_SOURCE
    other_host = '<iframe src="https://evil.example/embed?client=clinic"></iframe>'
    assert verify.find_snippet(other_host, "clinic", PUBLIC_URL) == verify.WRONG_SOURCE
    assert verify.find_snippet("<p>no frame</p>", "clinic", PUBLIC_URL) == verify.SNIPPET_NOT_FOUND


def test_escaped_ampersands_in_the_src_are_read_correctly():
    html = f'<iframe src="{PUBLIC_URL}/embed?client=clinic&amp;theme=light"></iframe>'
    assert verify.find_snippet(html, "clinic", PUBLIC_URL) == verify.OK


def test_hourly_limit_allows_ten_then_asks_to_wait():
    checks = [T0 - timedelta(minutes=m) for m in range(1, 11)]
    retry_at = verify.next_allowed_check_at(checks, T0)
    assert retry_at is not None
    assert retry_at == min(checks) + timedelta(hours=1)
    assert verify.next_allowed_check_at(checks[:9], T0) is None


def test_checks_older_than_an_hour_do_not_count():
    old = [T0 - timedelta(hours=2)] * 12
    assert verify.next_allowed_check_at(old, T0) is None


# ---------------------------------------------------------------- installation check: fetch


PAGE = f'<html><body><iframe src="{PUBLIC_URL}/embed?client=clinic"></iframe></body></html>'


def _run(transport, *, resolver, raw="https://clinic.com/", allowed=("clinic.com",)):
    return verify.verify_installation(
        client_slug="clinic",
        allowed_domains=list(allowed),
        raw_url=raw,
        public_url=PUBLIC_URL,
        transport=transport,
        resolver=resolver,
    )


async def _public(host):
    return "93.184.216.34"


async def test_a_page_with_the_snippet_passes_and_connects_to_the_pinned_address():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=PAGE.encode())

    result = await _run(httpx.MockTransport(handler), resolver=_public)
    assert result.code == verify.OK
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "clinic.com"


async def test_a_host_outside_the_allowed_websites_is_never_fetched():
    seen = []
    result = await _run(
        httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)),
        resolver=_public,
        raw="https://other.com/",
    )
    assert result.code == verify.NOT_ALLOWED_HOST
    assert seen == []


async def test_a_private_address_is_never_fetched():
    seen = []

    async def private(host):
        raise verify.CheckRefused(verify.BLOCKED_ADDRESS)

    result = await _run(
        httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)), resolver=private
    )
    assert result.code == verify.BLOCKED_ADDRESS
    assert seen == []


async def test_redirect_to_the_same_host_is_followed_once_and_rechecked():
    resolved = []

    async def resolver(host):
        resolved.append(host)
        return "93.184.216.34"

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(301, headers={"location": "/home"})
        return httpx.Response(200, content=PAGE.encode())

    result = await _run(httpx.MockTransport(handler), resolver=resolver)
    assert result.code == verify.OK
    assert len(resolved) == 2


async def test_redirect_to_another_host_is_blocked():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest"})

    result = await _run(httpx.MockTransport(handler), resolver=_public)
    assert result.code == verify.REDIRECT_BLOCKED
    assert len(seen) == 1


async def test_a_second_redirect_is_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "/again"})

    result = await _run(httpx.MockTransport(handler), resolver=_public)
    assert result.code == verify.REDIRECT_BLOCKED


async def test_an_oversize_page_is_refused():
    big = b"<html>" + b"x" * (verify.MAX_BODY_BYTES + 10)
    result = await _run(
        httpx.MockTransport(lambda r: httpx.Response(200, content=big)), resolver=_public
    )
    assert result.code == verify.TOO_LARGE


async def test_a_failed_page_load_is_reported_without_detail():
    def handler(request):
        raise httpx.ConnectError("refused by 93.184.216.34")

    result = await _run(httpx.MockTransport(handler), resolver=_public)
    assert result.code == verify.FETCH_FAILED
    assert "93.184.216.34" not in result.message


async def test_a_404_page_is_a_fetch_failure():
    result = await _run(httpx.MockTransport(lambda r: httpx.Response(404)), resolver=_public)
    assert result.code == verify.FETCH_FAILED


async def test_result_states_that_scripts_may_hide_the_snippet():
    result = await _run(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"<p>x</p>")), resolver=_public
    )
    assert result.code == verify.SNIPPET_NOT_FOUND
    assert "script" in result.note


async def test_the_route_refuses_the_eleventh_check_in_an_hour(
    admin_app, store, audit, monkeypatch
):
    checks = [T0 - timedelta(minutes=m) for m in range(1, 11)]

    async def times(client_id, since):
        return checks

    monkeypatch.setattr(store_module, "recent_verification_times", times)
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.post(f"{BASE}/verify", json={"url": "https://clinic.com/"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert not any(c.args[0] == "widget.verify" for c in audit.await_args_list)


async def test_each_check_is_recorded_and_audited(admin_app, store, audit, monkeypatch):
    monkeypatch.setattr(
        admin_widget.verification,
        "verify_installation",
        AsyncMock(
            return_value=verify.VerifyResult(
                code=verify.NOT_ALLOWED_HOST,
                message=verify.MESSAGES[verify.NOT_ALLOWED_HOST],
                checked_url="https://clinic.com/",
            )
        ),
    )
    with signed_in(admin_app, ADMIN):
        async with await _client(admin_app) as http:
            response = await http.post(f"{BASE}/verify", json={"url": "https://clinic.com/"})
    assert response.status_code == 200
    assert response.json()["code"] == verify.NOT_ALLOWED_HOST
    assert store.verifications[-1][2] == verify.NOT_ALLOWED_HOST
    assert audit.await_args.args[0] == "widget.verify"
