"""Who may see and change which clients and users (decisions 6 and 7, step 5 answers).

- Super Admin: all clients; creates Admin and Support users in any client; edits any
  non-super-admin user.
- Admin: sees users of their own client; creates and edits Support users there only.
- Support: no user management (blocked earlier by Area.users).
- Super Admins are never created or edited through the API (scripts/create_super_admin.py).
"""

import secrets
from uuid import UUID

import psycopg

from app.core.roles import USER_TYPE_IDS, UserType
from app.core.security import (
    hash_password,
    password_problem,
    token_cutoff_now,
    verify_password,
)
from app.db.models.client import (
    ClientRecord,
    get_client,
    insert_client,
    list_clients,
    update_client,
)
from app.db.models.user import UserRecord, get_user_by_id, insert_user, list_users, update_user

SUPER_ADMIN = UserType.super_admin.value
ADMIN = UserType.admin.value
SUPPORT = UserType.support.value


class UserManagementError(Exception):
    """Base class; each subclass maps to one HTTP status in the routes."""


class NotFoundError(UserManagementError):
    pass


class ForbiddenError(UserManagementError):
    pass


class ConflictError(UserManagementError):
    pass


class InvalidInputError(UserManagementError):
    pass


# ---------------------------------------------------------------- rules


def can_view_user(actor: UserRecord, target: UserRecord) -> bool:
    if actor.user_type_code == SUPER_ADMIN:
        return True
    if actor.user_type_code == ADMIN:
        return target.client_id is not None and target.client_id == actor.client_id
    return False


def can_manage_user(actor: UserRecord, target: UserRecord) -> bool:
    if target.user_type_code == SUPER_ADMIN:
        return False
    if actor.user_type_code == SUPER_ADMIN:
        return True
    if actor.user_type_code == ADMIN:
        return target.user_type_code == SUPPORT and target.client_id == actor.client_id
    return False


def _check_password(password: str, *, email: str) -> None:
    problem = password_problem(password, email=email)
    if problem:
        raise InvalidInputError(problem)


# ---------------------------------------------------------------- clients (Super Admin)


async def get_clients() -> list[ClientRecord]:
    return await list_clients()


async def get_client_or_404(client_id: UUID) -> ClientRecord:
    client = await get_client(client_id)
    if client is None:
        raise NotFoundError("Client not found")
    return client


async def create_client(*, name: str, slug: str) -> ClientRecord:
    try:
        return await insert_client(name=name.strip(), slug=slug.strip().lower())
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError("A client with this slug already exists") from exc


async def change_client(client_id: UUID, *, name: str | None, status: str | None) -> ClientRecord:
    # Suspending needs no session cleanup: sessions of a suspended client's users stop
    # resolving on their next request (services.auth.is_user_allowed).
    await get_client_or_404(client_id)
    updated = await update_client(client_id, name=name.strip() if name else None, status=status)
    if updated is None:
        raise NotFoundError("Client not found")
    return updated


# ---------------------------------------------------------------- users


async def get_visible_users(
    actor: UserRecord, *, client_id: UUID | None = None
) -> list[UserRecord]:
    if actor.user_type_code == SUPER_ADMIN:
        return await list_users(client_id=client_id)
    if client_id is not None and client_id != actor.client_id:
        raise ForbiddenError("You can only see users of your own client")
    return await list_users(client_id=actor.client_id)


async def get_visible_user(actor: UserRecord, user_id: UUID) -> UserRecord:
    target = await get_user_by_id(user_id)
    # Same 404 for "doesn't exist" and "not yours", so IDs of other clients aren't confirmed.
    if target is None or not can_view_user(actor, target):
        raise NotFoundError("User not found")
    return target


async def create_user(
    actor: UserRecord,
    *,
    email: str,
    full_name: str,
    password: str | None,
    user_type: str,
    client_id: UUID | None,
) -> UserRecord:
    """password=None: the account gets no usable password. The caller sends an invite, and the
    user sets their own password from it. With a password, the admin's choice must be changed
    at first sign-in (the legacy path)."""
    if user_type not in (ADMIN, SUPPORT):
        raise ForbiddenError("Super Admins can only be created with scripts/create_super_admin.py")

    if actor.user_type_code == ADMIN:
        if user_type != SUPPORT:
            raise ForbiddenError("Admins can only create Support users")
        if client_id is not None and client_id != actor.client_id:
            raise ForbiddenError("Admins can only add users to their own client")
        client_id = actor.client_id
    elif actor.user_type_code != SUPER_ADMIN:
        raise ForbiddenError("You cannot create users")

    if client_id is None:
        raise InvalidInputError("client_id is required for Admin and Support users")
    client = await get_client(client_id)
    if client is None:
        raise NotFoundError("Client not found")
    if client.status != "active":
        raise InvalidInputError("Cannot add users to a suspended client")

    email = email.strip().lower()
    full_name = full_name.strip()
    if not full_name:
        raise InvalidInputError("Full name is required")
    if password is None:
        # Random and never shown: no one knows it, and it fails every verify_password check.
        password_hash = hash_password(secrets.token_urlsafe(32))
        must_change = False
    else:
        _check_password(password, email=email)
        password_hash = hash_password(password)
        # The admin chose this password; the user replaces it at first sign-in.
        must_change = True

    try:
        return await insert_user(
            client_id=client_id,
            user_type_id=USER_TYPE_IDS[UserType(user_type)],
            email=email,
            password_hash=password_hash,
            must_change_password=must_change,
            full_name=full_name,
        )
    except psycopg.errors.UniqueViolation as exc:
        raise ConflictError("A user with this email already exists") from exc


async def change_user(
    actor: UserRecord,
    user_id: UUID,
    *,
    full_name: str | None,
    is_active: bool | None,
    password: str | None,
) -> UserRecord:
    target = await get_visible_user(actor, user_id)
    if not can_manage_user(actor, target):
        raise ForbiddenError("You cannot change this user")

    if full_name is not None and not full_name.strip():
        raise InvalidInputError("Full name cannot be empty")
    new_hash = None
    if password is not None:
        _check_password(password, email=target.email)
        new_hash = hash_password(password)

    # Deactivation or a password reset ends every token the user already has. (Inactive
    # users are refused anyway; the cutoff also keeps old tokens dead after reactivation.)
    cutoff = token_cutoff_now() if is_active is False or new_hash is not None else None
    updated = await update_user(
        target.id,
        full_name=full_name.strip() if full_name is not None else None,
        is_active=is_active,
        password_hash=new_hash,
        tokens_valid_after=cutoff,
        # An admin-set password must be replaced by the user at their next sign-in.
        must_change_password=True if new_hash is not None else None,
    )
    if updated is None:
        raise NotFoundError("User not found")
    return updated


async def change_own_password(
    user: UserRecord,
    *,
    current_password: str,
    new_password: str,
) -> None:
    """Ends all of the user's existing tokens; the caller issues a fresh one for this browser."""
    if not verify_password(current_password, user.password_hash):
        raise InvalidInputError("Current password is incorrect")
    if new_password == current_password:
        raise InvalidInputError("New password must be different from the current one")
    _check_password(new_password, email=user.email)
    await update_user(
        user.id,
        password_hash=hash_password(new_password),
        tokens_valid_after=token_cutoff_now(),
        must_change_password=False,
    )
