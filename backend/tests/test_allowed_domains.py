"""Allowed websites for the widget: cleaning, validation, the 50-entry cap, and the admin route.

Route tests run with the real normalization. Only the database write is replaced.
"""

from unittest.mock import AsyncMock, patch

import pytest
from auth_helpers import make_client_record, signed_in
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import settings as admin_settings
from app.core.permissions import AREA_ACCESS, Area, can_access
from app.core.roles import UserType
from app.db.models.client import (
    MAX_ALLOWED_DOMAINS,
    clean_domain_entry,
    normalize_allowed_domains,
)

SA, ADMIN, SUPPORT = UserType.super_admin, UserType.admin, UserType.support


# ---------------------------------------------------------------- cleaning


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("https://www.Clinic.com/contact?x=1#top", "www.clinic.com"),
        ("http://clinic.com/", "clinic.com"),
        ("  clinic.com  ", "clinic.com"),
        ("clinic.com:443", "clinic.com"),
        ("clinic.com:80", "clinic.com"),
        ("https://clinic.com:443/", "clinic.com"),
        ("localhost:3000", "localhost:3000"),
        ("clinic.com:8443", "clinic.com:8443"),
        ("clinic.com.", "clinic.com"),
        ("*.clinic.com", "*.clinic.com"),
    ],
)
def test_clean_domain_entry_strips_what_is_not_part_of_the_host(typed, expected):
    assert clean_domain_entry(typed) == expected


def test_clean_domain_entry_keeps_userinfo_so_validation_refuses_it():
    cleaned = clean_domain_entry("https://admin@clinic.com")
    with pytest.raises(ValueError):
        normalize_allowed_domains([cleaned])


# ---------------------------------------------------------------- normalization


def test_valid_patterns_are_kept_in_order_and_deduplicated():
    result = normalize_allowed_domains(
        ["Clinic.com", "*.clinic.com", "https://clinic.com/", "localhost:3000", " "]
    )
    assert result == ["clinic.com", "*.clinic.com", "localhost:3000"]


@pytest.mark.parametrize("bad", ["*.com", "*.", "*", "*.*.clinic.com", "clinic..com"])
def test_bare_tld_and_malformed_wildcards_are_refused(bad):
    with pytest.raises(ValueError):
        normalize_allowed_domains([bad])


def test_two_label_wildcard_is_allowed():
    assert normalize_allowed_domains(["*.clinic.com"]) == ["*.clinic.com"]


@pytest.mark.parametrize("bad", ["clinic.com:70000", "clinic.com:0"])
def test_ports_outside_range_are_refused(bad):
    with pytest.raises(ValueError, match="port outside"):
        normalize_allowed_domains([bad])


@pytest.mark.parametrize("bad", ["example.com; script-src *", "exa mple.com", "a'b.com"])
def test_characters_that_could_break_the_csp_header_are_refused(bad):
    with pytest.raises(ValueError):
        normalize_allowed_domains([bad])


def test_error_message_names_the_bad_entry():
    with pytest.raises(ValueError, match="exa mple.com"):
        normalize_allowed_domains(["exa mple.com"])


def test_fifty_entries_are_allowed_and_fifty_one_are_refused():
    fifty = [f"site{i}.clinic.com" for i in range(MAX_ALLOWED_DOMAINS)]
    assert len(normalize_allowed_domains(fifty)) == MAX_ALLOWED_DOMAINS
    with pytest.raises(ValueError, match="At most 50"):
        normalize_allowed_domains(fifty + ["extra.clinic.com"])


def test_empty_list_is_valid_and_means_no_sites():
    assert normalize_allowed_domains([]) == []


# ---------------------------------------------------------------- permissions


def test_only_super_admin_and_admin_may_change_widget_domains():
    assert AREA_ACCESS[Area.widget_domains] == frozenset({"super_admin", "admin"})
    assert can_access("admin", Area.widget_domains)
    assert can_access("super_admin", Area.widget_domains)
    assert not can_access("support", Area.widget_domains)


# ---------------------------------------------------------------- routes


@pytest.fixture
def domains_app():
    test_app = FastAPI()
    test_app.include_router(admin_settings.router, prefix="/api/v1")
    return test_app


@pytest.fixture
def known_client(monkeypatch):
    """Any client id resolves; the record starts with the given domains."""
    state = {"domains": ["old.clinic.com"]}

    async def get_client(client_id):
        record = make_client_record(client_id=client_id)
        record.allowed_domains = list(state["domains"])
        return record

    monkeypatch.setattr("app.services.client_scope.get_client", get_client)
    return state


def _save_stub(calls: list):
    """Stands in for the database write: real normalization, then the saved record."""

    async def save(client_id, patterns):
        cleaned = normalize_allowed_domains(patterns)
        calls.append((client_id, cleaned))
        record = make_client_record(client_id=client_id)
        record.allowed_domains = cleaned
        return record

    return save


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_admin_reads_own_client_domains(domains_app, known_client):
    with signed_in(domains_app, ADMIN):
        async with _client(domains_app) as client:
            response = await client.get("/api/v1/admin/settings/allowed-domains")
    assert response.status_code == 200
    assert response.json() == {"domains": ["old.clinic.com"]}


async def test_put_saves_cleaned_domains_and_is_audited(domains_app, known_client, audit_rows):
    calls: list = []
    with (
        signed_in(domains_app, ADMIN) as actor,
        patch(
            "app.api.routes.admin.settings.set_client_allowed_domains",
            new=AsyncMock(side_effect=_save_stub(calls)),
        ),
    ):
        async with _client(domains_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/allowed-domains",
                json={"domains": ["https://www.Clinic.com/", "*.clinic.com", "localhost:3000"]},
            )

    assert response.status_code == 200
    assert response.json() == {"domains": ["www.clinic.com", "*.clinic.com", "localhost:3000"]}
    assert calls[0][0] == actor.client_id
    assert [(r["action"], r["client_id"]) for r in audit_rows] == [
        ("settings.allowed_domains_update", actor.client_id)
    ]


async def test_put_empty_list_clears_the_domains(domains_app, known_client):
    with (
        signed_in(domains_app, ADMIN),
        patch(
            "app.api.routes.admin.settings.set_client_allowed_domains",
            new=AsyncMock(side_effect=_save_stub([])),
        ),
    ):
        async with _client(domains_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/allowed-domains", json={"domains": []}
            )
    assert response.status_code == 200
    assert response.json() == {"domains": []}


@pytest.mark.parametrize("bad", ["*.com", "exa mple.com", "clinic.com:99999"])
async def test_put_with_an_invalid_entry_is_400_and_nothing_is_saved(
    domains_app, known_client, bad
):
    save = AsyncMock(side_effect=_save_stub([]))
    with (
        signed_in(domains_app, ADMIN),
        patch("app.api.routes.admin.settings.set_client_allowed_domains", new=save),
    ):
        async with _client(domains_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/allowed-domains", json={"domains": ["clinic.com", bad]}
            )
    assert response.status_code == 400
    assert (
        bad.strip() in response.json()["detail"] or "not a site name" in response.json()["detail"]
    )
    save.assert_awaited_once()  # the route called the write; the write refused the list


async def test_put_with_more_than_fifty_entries_is_422(domains_app, known_client):
    with signed_in(domains_app, ADMIN):
        async with _client(domains_app) as client:
            response = await client.put(
                "/api/v1/admin/settings/allowed-domains",
                json={"domains": [f"s{i}.clinic.com" for i in range(51)]},
            )
    assert response.status_code == 422


async def test_support_is_refused_with_403(domains_app, known_client):
    with signed_in(domains_app, SUPPORT):
        async with _client(domains_app) as client:
            get_response = await client.get("/api/v1/admin/settings/allowed-domains")
            put_response = await client.put(
                "/api/v1/admin/settings/allowed-domains", json={"domains": ["clinic.com"]}
            )
    assert get_response.status_code == 403
    assert put_response.status_code == 403


async def test_admin_cannot_name_another_clients_id(domains_app, known_client):
    other = "00000000-0000-0000-0000-000000000042"
    with signed_in(domains_app, ADMIN):
        async with _client(domains_app) as client:
            response = await client.put(
                f"/api/v1/admin/settings/allowed-domains?client_id={other}",
                json={"domains": ["clinic.com"]},
            )
    assert response.status_code == 403


async def test_super_admin_without_a_client_is_400(domains_app, known_client):
    with signed_in(domains_app, SA):
        async with _client(domains_app) as client:
            get_response = await client.get("/api/v1/admin/settings/allowed-domains")
            put_response = await client.put(
                "/api/v1/admin/settings/allowed-domains", json={"domains": ["clinic.com"]}
            )
    assert get_response.status_code == 400
    assert put_response.status_code == 400


async def test_super_admin_may_set_a_chosen_client(domains_app, known_client):
    chosen = "00000000-0000-0000-0000-000000000077"
    calls: list = []
    with (
        signed_in(domains_app, SA),
        patch(
            "app.api.routes.admin.settings.set_client_allowed_domains",
            new=AsyncMock(side_effect=_save_stub(calls)),
        ),
    ):
        async with _client(domains_app) as client:
            response = await client.put(
                f"/api/v1/admin/settings/allowed-domains?client_id={chosen}",
                json={"domains": ["clinic.com"]},
            )
    assert response.status_code == 200
    assert str(calls[0][0]) == chosen
