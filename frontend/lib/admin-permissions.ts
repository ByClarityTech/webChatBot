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
  | "clients";

const AREA_ACCESS: Record<AdminArea, readonly UserType[]> = {
  dashboard: ["super_admin", "admin", "support"],
  documents: ["super_admin", "admin"],
  calendar: ["super_admin", "admin", "support"],
  business_hours: ["super_admin", "admin", "support"],
  bookings: ["super_admin", "admin", "support"],
  audit_log: ["super_admin", "admin"],
  users: ["super_admin", "admin"],
  clients: ["super_admin"],
};

export function canAccess(userType: UserType, area: AdminArea): boolean {
  return AREA_ACCESS[area].includes(userType);
}

export const USER_TYPE_LABELS: Record<UserType, string> = {
  super_admin: "Super Admin",
  admin: "Admin",
  support: "Support",
};

// Admin pages and the area each one needs. Longest prefix wins in areaForPath.
export const ADMIN_PAGES: readonly { href: string; label: string; area: AdminArea }[] = [
  { href: "/admin", label: "Home", area: "dashboard" },
  { href: "/admin/documents", label: "Documents", area: "documents" },
  { href: "/admin/calendar", label: "Calendar", area: "calendar" },
  { href: "/admin/settings", label: "Settings", area: "business_hours" },
  { href: "/admin/bookings", label: "Bookings", area: "bookings" },
];

export function areaForPath(pathname: string): AdminArea | null {
  const match = [...ADMIN_PAGES]
    .sort((a, b) => b.href.length - a.href.length)
    .find((page) => pathname === page.href || pathname.startsWith(`${page.href}/`));
  return match ? match.area : null;
}
