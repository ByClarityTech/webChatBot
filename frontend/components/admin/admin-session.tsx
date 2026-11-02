"use client";

import { createContext, useContext } from "react";

import type { AdminUser } from "@/lib/admin-api";

const AdminSessionContext = createContext<AdminUser | null>(null);

export const AdminSessionProvider = AdminSessionContext.Provider;

/** The signed-in admin user. Only valid inside the admin layout, after sign-in. */
export function useAdminUser(): AdminUser {
  const user = useContext(AdminSessionContext);
  if (!user) {
    throw new Error("useAdminUser must be used inside the signed-in admin layout");
  }
  return user;
}
