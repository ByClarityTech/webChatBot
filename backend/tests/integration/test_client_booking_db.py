"""Per-client bookings, Google connections, business hours and OAuth flows (migration 0007)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from app.db.models.admin_settings import get_client_business_hours, save_client_business_hours
from app.db.models.booking import (
    PENDING_HOLD_MINUTES,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    claim_booking_slot,
    confirm_booking,
    get_client_booking,
    list_blocking_bookings,
)
from app.db.models.client import get_client, set_client_calendar_mode
from app.db.models.google_token import (
    STATUS_NEEDS_RECONNECT,
    STATUS_OK,
    delete_client_google_token,
    get_client_google_token,
    mark_client_google_token_needs_reconnect,
    save_client_google_token,
)
from app.db.models.oauth_pending import consume_oauth_pending_flow, save_oauth_pending_state

pytestmark = pytest.mark.integration

SLOT = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)


def add_client(db, slug):
    return db.execute(
        "INSERT INTO clients (name, slug) VALUES (%s, %s) RETURNING id", (slug.upper(), slug)
    ).fetchone()[0]


async def claim(client_id, slot=SLOT, email="v@example.com"):
    return await claim_booking_slot(
        client_id=client_id,
        name="Visitor",
        phone="0005550100",
        email=email,
        slot_start=slot,
        slot_end=slot + timedelta(minutes=30),
        thread_id=None,
    )


# ---------------------------------------------------------------- bookings


async def test_a_claim_holds_the_slot_as_pending_until_confirmed(db):
    client_a = add_client(db, "a")
    held = await claim(client_a)
    assert (held.status, held.client_id, held.google_event_id) == (STATUS_PENDING, client_a, None)

    with pytest.raises(psycopg.errors.ExclusionViolation):
        await claim(client_a, email="second@example.com")

    confirmed = await confirm_booking(held.id, "google-event-1")
    assert (confirmed.status, confirmed.google_event_id) == (STATUS_CONFIRMED, "google-event-1")
    # Only a pending booking can be confirmed.
    assert await confirm_booking(held.id, "again") is None
    with pytest.raises(psycopg.errors.ExclusionViolation):
        await claim(client_a)


async def test_a_pending_hold_left_behind_expires(db):
    client_a = add_client(db, "a")
    stale = await claim(client_a)
    db.execute(
        "UPDATE bookings SET created_at = NOW() - make_interval(mins => %s) WHERE id = %s",
        (PENDING_HOLD_MINUTES + 1, stale.id),
    )

    fresh = await claim(client_a, email="next@example.com")

    assert fresh.id != stale.id
    assert db.execute("SELECT COUNT(*) FROM bookings WHERE id = %s", (stale.id,)).fetchone()[0] == 0


async def test_a_recent_pending_hold_still_blocks(db):
    client_a = add_client(db, "a")
    held = await claim(client_a)
    db.execute(
        "UPDATE bookings SET created_at = NOW() - make_interval(mins => %s) WHERE id = %s",
        (PENDING_HOLD_MINUTES - 1, held.id),
    )
    with pytest.raises(psycopg.errors.ExclusionViolation):
        await claim(client_a, email="next@example.com")


async def test_booking_lookups_stay_inside_one_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    booking = await claim(client_a)
    assert (await get_client_booking(booking.id, client_id=client_a)).id == booking.id
    assert await get_client_booking(booking.id, client_id=client_b) is None
    assert await get_client_booking(uuid4(), client_id=client_a) is None


# ---------------------------------------------------------------- Google connections


async def save_token(client_id, email="owner@example.com"):
    return await save_client_google_token(
        client_id,
        access_token="access",
        refresh_token_encrypted="encrypted-refresh",
        token_expiry=datetime(2030, 1, 1, tzinfo=UTC),
        calendar_id="primary",
        connected_email=email,
    )


async def test_each_client_has_its_own_connection_and_health(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    first = await save_token(client_a, "a@example.com")
    await save_token(client_b, "b@example.com")
    assert (first.status, first.client_id) == (STATUS_OK, client_a)

    await mark_client_google_token_needs_reconnect(
        client_a, "invalid_grant: Token has been revoked"
    )
    broken = await get_client_google_token(client_a)
    assert (broken.status, broken.last_error) == (
        STATUS_NEEDS_RECONNECT,
        "invalid_grant: Token has been revoked",
    )
    assert (await get_client_google_token(client_b)).status == STATUS_OK

    # Connecting again replaces the connection and clears the error.
    again = await save_token(client_a, "a2@example.com")
    assert (again.status, again.last_error, again.connected_email) == (
        STATUS_OK,
        None,
        "a2@example.com",
    )
    assert db.execute("SELECT COUNT(*) FROM google_tokens").fetchone()[0] == 2

    assert await delete_client_google_token(client_a) is True
    assert await get_client_google_token(client_a) is None
    assert (await get_client_google_token(client_b)).connected_email == "b@example.com"


# ---------------------------------------------------------------- business hours


async def test_business_hours_are_per_client(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    assert await get_client_business_hours(client_a) is None

    hours = dict(
        business_days=[1, 2, 3],
        start_time="08:00",
        end_time="12:00",
        slot_duration_minutes=20,
        slot_buffer_minutes=5,
        min_notice_minutes=30,
    )
    await save_client_business_hours(client_a, timezone="Asia/Kolkata", **hours)
    await save_client_business_hours(client_b, timezone="Europe/London", **hours)
    updated = await save_client_business_hours(
        client_a, timezone="Asia/Kolkata", **{**hours, "end_time": "13:00"}
    )

    assert (updated.end_time, updated.timezone, updated.min_notice_minutes) == (
        "13:00",
        "Asia/Kolkata",
        30,
    )
    assert (await get_client_business_hours(client_b)).timezone == "Europe/London"
    assert db.execute("SELECT COUNT(*) FROM business_hours_settings").fetchone()[0] == 2


# ---------------------------------------------------------------- OAuth connect flows


async def test_an_oauth_flow_remembers_its_client_and_user_and_is_single_use(db):
    client_a = add_client(db, "a")
    user_id = db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name)"
        " VALUES (%s, 2, 'admin@example.com', 'h', 'Admin') RETURNING id",
        (client_a,),
    ).fetchone()[0]
    await save_oauth_pending_state("state-1", "verifier-1", client_id=client_a, user_id=user_id)

    flow = await consume_oauth_pending_flow("state-1")

    assert (flow.code_verifier, flow.client_id, flow.user_id) == ("verifier-1", client_a, user_id)
    assert await consume_oauth_pending_flow("state-1") is None
    assert await consume_oauth_pending_flow("never-issued") is None


async def test_an_expired_oauth_flow_is_refused(db):
    client_a = add_client(db, "a")
    user_id = db.execute(
        "INSERT INTO users (client_id, user_type_id, email, password_hash, full_name)"
        " VALUES (%s, 2, 'admin@example.com', 'h', 'Admin') RETURNING id",
        (client_a,),
    ).fetchone()[0]
    await save_oauth_pending_state("state-old", "verifier", client_id=client_a, user_id=user_id)
    db.execute("UPDATE oauth_pending_states SET expires_at = NOW() - interval '1 minute'")
    assert await consume_oauth_pending_flow("state-old") is None


# ---------------------------------------------------------------- after migration 0008


async def test_two_clients_can_hold_the_same_time_and_cancelling_frees_a_slot(db):
    client_a, client_b = add_client(db, "a"), add_client(db, "b")
    first = await claim(client_a)
    await claim(client_b)  # same slot start, other client: allowed now

    db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = %s", (first.id,))
    again = await claim(client_a, email="again@example.com")
    assert again.status == STATUS_PENDING


async def test_rows_must_have_a_client(db):
    with pytest.raises(psycopg.errors.NotNullViolation), db.transaction():
        db.execute(
            "INSERT INTO bookings (name, phone, email, slot_start, slot_end, status)"
            " VALUES ('V', '1', 'v@example.com', NOW(), NOW(), 'pending')"
        )
    with pytest.raises(psycopg.errors.NotNullViolation), db.transaction():
        db.execute("INSERT INTO oauth_pending_states (state, code_verifier, expires_at)"
                   " VALUES ('s', 'v', NOW())")


# ---------------------------------------------------------------- migration 0009


async def test_overlapping_bookings_are_refused_even_with_another_start(db):
    client_a = add_client(db, "a")
    await claim(client_a)  # 09:00-09:30
    with pytest.raises(psycopg.errors.ExclusionViolation):
        await claim_booking_slot(
            client_id=client_a,
            name="V",
            phone="1",
            email="v2@example.com",
            slot_start=SLOT + timedelta(minutes=20),
            slot_end=SLOT + timedelta(minutes=40),
            thread_id=None,
        )
    # Back to back is fine.
    await claim_booking_slot(
        client_id=client_a,
        name="V",
        phone="1",
        email="v3@example.com",
        slot_start=SLOT + timedelta(minutes=30),
        slot_end=SLOT + timedelta(minutes=60),
        thread_id=None,
    )


async def test_blocking_bookings_skip_stale_holds_and_follow_the_mode(db):
    client_a = add_client(db, "a")
    fresh = await claim(client_a)
    stale = await claim(client_a, slot=SLOT + timedelta(hours=1), email="stale@example.com")
    db.execute(
        "UPDATE bookings SET created_at = NOW() - make_interval(mins => %s) WHERE id = %s",
        (PENDING_HOLD_MINUTES + 1, stale.id),
    )
    done = await claim(client_a, slot=SLOT + timedelta(hours=2), email="done@example.com")
    await confirm_booking(done.id, None)
    window = dict(window_start=SLOT - timedelta(hours=1), window_end=SLOT + timedelta(hours=4))

    built_in = await list_blocking_bookings(client_a, include_confirmed=True, **window)
    google = await list_blocking_bookings(client_a, include_confirmed=False, **window)

    assert [start for start, _ in built_in] == [fresh.slot_start, done.slot_start]
    assert [start for start, _ in google] == [fresh.slot_start]  # Google shows confirmed ones


async def test_calendar_mode_is_stored_and_checked(db):
    client_a = add_client(db, "a")
    assert (await get_client(client_a)).calendar_mode == "none"
    assert (await set_client_calendar_mode(client_a, "built_in")).calendar_mode == "built_in"
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute("UPDATE clients SET calendar_mode = 'outlook' WHERE id = %s", (client_a,))
