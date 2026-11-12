// Mirrors backend/app/core/permissions.py. The API enforces access; this only decides what
// the admin UI shows. Keep the two in step.

export type UserType = "super_admin" | "admin" | "support";

export type AdminArea =
  | "dashboard"
  | "documents"
  | "calendar"
  | "business_hours"
  | "bookings"
  | "audit_log"
  | "users"
  | "clients"
  | "widget_domains"
  | "practice_contact";

const AREA_ACCESS: Record<AdminArea, readonly UserType[]> = {
  dashboard: ["super_admin", "admin", "support"],
  documents: ["super_admin", "admin"],
  calendar: ["super_admin", "admin", "support"],
  business_hours: ["super_admin", "admin", "support"],
  bookings: ["super_admin", "admin", "support"],
  audit_log: ["super_admin", "admin"],
  users: ["super_admin", "admin"],
  clients: ["super_admin"],
  widget_domains: ["super_admin", "admin"],
  practice_contact: ["super_admin", "admin"],
};

export function canAccess(userType: UserType, area: AdminArea): boolean {
  return AREA_ACCESS[area].includes(userType);
}

export const USER_TYPE_LABELS: Record<UserType, string> = {
  super_admin: "Super Admin",
  admin: "Admin",
  support: "Support",
};

// User management rules; mirror backend/app/services/user_management.py so the UI only
// shows actions the API will accept.

type UserRef = { user_type: UserType; client_id: string | null };

/** User types an actor may create. Super Admins are never created in the app. */
export function creatableUserTypes(actor: UserType): ("admin" | "support")[] {
  if (actor === "super_admin") {
    return ["admin", "support"];
  }
  if (actor === "admin") {
    return ["support"];
  }
  return [];
}

/** Rename, reset password, activate or deactivate. */
export function canManageUser(actor: UserRef, target: UserRef): boolean {
  if (target.user_type === "super_admin") {
    return false;
  }
  if (actor.user_type === "super_admin") {
    return true;
  }
  if (actor.user_type === "admin") {
    return target.user_type === "support" && target.client_id === actor.client_id;
  }
  return false;
}

// Admin pages and the area each one needs. Longest prefix wins in areaForPath.
export const ADMIN_PAGES: readonly { href: string; label: string; area: AdminArea }[] = [
  { href: "/admin", label: "Home", area: "dashboard" },
  { href: "/admin/documents", label: "Documents", area: "documents" },
  // FAQs are part of the knowledge base: same roles as Documents.
  { href: "/admin/faqs", label: "FAQs", area: "documents" },
  { href: "/admin/calendar", label: "Calendar", area: "calendar" },
  { href: "/admin/settings", label: "Settings", area: "business_hours" },
  { href: "/admin/bookings", label: "Bookings", area: "bookings" },
  { href: "/admin/users", label: "Users", area: "users" },
  { href: "/admin/clients", label: "Clients", area: "clients" },
  { href: "/admin/audit-log", label: "Audit log", area: "audit_log" },
];

export function areaForPath(pathname: string): AdminArea | null {
  const match = [...ADMIN_PAGES]
    .sort((a, b) => b.href.length - a.href.length)
    .find((page) => pathname === page.href || pathname.startsWith(`${page.href}/`));
  return match ? match.area : null;
}
