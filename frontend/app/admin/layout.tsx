"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AdminSessionProvider } from "@/components/admin/admin-session";
import { adminLogout, getCurrentUser, LOGIN_PATH, type AdminUser } from "@/lib/admin-api";
import { ADMIN_PAGES, areaForPath, canAccess, USER_TYPE_LABELS } from "@/lib/admin-permissions";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AdminUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isLoginPage = pathname === LOGIN_PATH;

  useEffect(() => {
    if (isLoginPage) {
      return;
    }
    let cancelled = false;

    async function loadSession() {
      try {
        const current = await getCurrentUser();
        if (cancelled) {
          return;
        }
        if (!current) {
          router.replace(LOGIN_PATH);
          return;
        }
        setUser(current);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not reach the API.");
        }
      }
    }

    void loadSession();
    return () => {
      cancelled = true;
    };
  }, [isLoginPage, router]);

  async function handleSignOut() {
    try {
      await adminLogout();
    } finally {
      setUser(null);
      router.replace(LOGIN_PATH);
    }
  }

  if (isLoginPage) {
    return <>{children}</>;
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
        Loading...
      </div>
    );
  }

  const navItems = ADMIN_PAGES.filter((page) => canAccess(user.user_type, page.area));
  const area = areaForPath(pathname);
  const allowed = area === null || canAccess(user.user_type, area);

  return (
    <AdminSessionProvider value={user}>
      <div className="flex min-h-screen">
        <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-5 py-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Admin</p>
            <h1 className="text-lg font-semibold text-slate-900">Dashboard</h1>
          </div>

          <nav className="flex-1 space-y-1 px-3 py-4">
            {navItems.map((item) => {
              const active =
                item.href === "/admin"
                  ? pathname === "/admin"
                  : pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`block rounded-lg px-3 py-2 text-sm font-medium ${
                    active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-slate-200 p-3">
            <div className="px-3 pb-3">
              <p className="truncate text-sm font-medium text-slate-900">{user.full_name}</p>
              <p className="truncate text-xs text-slate-500">{user.email}</p>
              <p className="mt-1 text-xs text-slate-500">
                {USER_TYPE_LABELS[user.user_type]}
                {user.client ? ` · ${user.client.name}` : " · All clients"}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void handleSignOut()}
              className="w-full rounded-lg px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-100"
            >
              Sign out
            </button>
          </div>
        </aside>

        <main className="min-w-0 flex-1 bg-slate-50">
          {allowed ? (
            children
          ) : (
            <div className="px-8 py-10">
              <h1 className="text-2xl font-semibold text-slate-900">No access</h1>
              <p className="mt-2 max-w-2xl text-sm text-slate-600">
                Your role ({USER_TYPE_LABELS[user.user_type]}) cannot open this page. Use the
                menu to go to a page you can use.
              </p>
            </div>
          )}
        </main>
      </div>
    </AdminSessionProvider>
  );
}
