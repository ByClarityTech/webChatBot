"use client";

import { createContext, useContext } from "react";

import type { AdminUser } from "@/lib/admin-api";

type AdminSession = {
  user: AdminUser;
  /** Reload the signed-in user from /auth/me (e.g. after a password change). */
  refresh: () => Promise<void>;
};

const AdminSessionContext = createContext<AdminSession | null>(null);

export const AdminSessionProvider = AdminSessionContext.Provider;

function useAdminSession(): AdminSession {
  const session = useContext(AdminSessionContext);
  if (!session) {
    throw new Error("Admin session hooks must be used inside the signed-in admin layout");
  }
  return session;
}

/** The signed-in admin user. Only valid inside the admin layout, after sign-in. */
export function useAdminUser(): AdminUser {
  return useAdminSession().user;
}

export function useRefreshAdminUser(): () => Promise<void> {
  return useAdminSession().refresh;
}
