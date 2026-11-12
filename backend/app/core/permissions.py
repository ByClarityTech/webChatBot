from enum import Enum

from app.core.roles import UserType

SUPER_ADMIN = UserType.super_admin.value
ADMIN = UserType.admin.value
SUPPORT = UserType.support.value


class Area(str, Enum):
    dashboard = "dashboard"
    documents = "documents"
    calendar = "calendar"
    business_hours = "business_hours"
    bookings_view = "bookings_view"
    bookings_cancel = "bookings_cancel"
    audit_log = "audit_log"
    users = "users"
    clients = "clients"
    widget_domains = "widget_domains"


# Who may use each admin area (decision 7). Client scoping ("own client only") is a
# separate check, added with the multi-tenant work; this map only decides by role.
AREA_ACCESS: dict[Area, frozenset[str]] = {
    Area.dashboard: frozenset({SUPER_ADMIN, ADMIN, SUPPORT}),
    Area.documents: frozenset({SUPER_ADMIN, ADMIN}),
    Area.calendar: frozenset({SUPER_ADMIN, ADMIN, SUPPORT}),
    Area.business_hours: frozenset({SUPER_ADMIN, ADMIN, SUPPORT}),
    Area.bookings_view: frozenset({SUPER_ADMIN, ADMIN, SUPPORT}),
    Area.bookings_cancel: frozenset({SUPER_ADMIN, ADMIN, SUPPORT}),
    Area.audit_log: frozenset({SUPER_ADMIN, ADMIN}),
    Area.users: frozenset({SUPER_ADMIN, ADMIN}),
    Area.clients: frozenset({SUPER_ADMIN}),
    # Who may set the sites that can embed the widget. Admins only reach their own client,
    # through resolve_client_scope. Support cannot change it.
    Area.widget_domains: frozenset({SUPER_ADMIN, ADMIN}),
}


def can_access(user_type_code: str, area: Area) -> bool:
    return user_type_code in AREA_ACCESS[area]
