"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearAdminApiKey, getAdminApiKey } from "@/lib/admin-api";

const NAV_ITEMS = [
  { href: "/admin/documents", label: "Documents", comingSoon: false },
  { href: "/admin/calendar", label: "Calendar", comingSoon: false },
  { href: "/admin/settings", label: "Settings", comingSoon: false },
  { href: "/admin/bookings", label: "Bookings", comingSoon: false },
] as const;

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const isLoginPage = pathname === "/admin/login";

  useEffect(() => {
    if (isLoginPage) {
      setReady(true);
      return;
    }

    if (!getAdminApiKey()) {
      router.replace("/admin/login");
      return;
    }

    setReady(true);
  }, [isLoginPage, pathname, router]);

  function handleSignOut() {
    clearAdminApiKey();
    router.replace("/admin/login");
  }

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
        Loading...
      </div>
    );
  }

  if (isLoginPage) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Admin</p>
          <h1 className="text-lg font-semibold text-slate-900">Dashboard</h1>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-4">
          <Link
            href="/admin"
            className={`block rounded-lg px-3 py-2 text-sm font-medium ${
              pathname === "/admin"
                ? "bg-slate-900 text-white"
                : "text-slate-700 hover:bg-slate-100"
            }`}
          >
            Home
          </Link>

          {NAV_ITEMS.map((item) =>
            item.comingSoon ? (
              <span
                key={item.href}
                className="flex items-center justify-between rounded-lg px-3 py-2 text-sm text-slate-400"
                aria-disabled="true"
              >
                {item.label}
                <span className="text-xs">Soon</span>
              </span>
            ) : (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm font-medium ${
                  pathname === item.href || pathname.startsWith(`${item.href}/`)
                    ? "bg-slate-900 text-white"
                    : "text-slate-700 hover:bg-slate-100"
                }`}
              >
                {item.label}
              </Link>
            ),
          )}
        </nav>

        <div className="border-t border-slate-200 p-3">
          <button
            type="button"
            onClick={handleSignOut}
            className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-100"
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 bg-slate-50">{children}</main>
    </div>
  );
}
