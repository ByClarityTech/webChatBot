from enum import Enum


class UserType(str, Enum):
    """Matches user_types.code. IDs are fixed by migration 0002_auth_tables."""

    super_admin = "super_admin"
    admin = "admin"
    support = "support"


USER_TYPE_IDS: dict[UserType, int] = {
    UserType.super_admin: 1,
    UserType.admin: 2,
    UserType.support: 3,
}
