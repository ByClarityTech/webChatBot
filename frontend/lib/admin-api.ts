import type { UserType } from "@/lib/admin-permissions";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const LOGIN_PATH = "/admin/login";

export type AdminClient = {
  id: string;
  name: string;
  slug: string;
};

export type AdminUser = {
  id: string;
  email: string;
  full_name: string;
  user_type: UserType;
  client: AdminClient | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
  }
}

async function parseApiError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string | Array<{ msg?: string }> };
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) {
        return messages.join(", ");
      }
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

async function toApiError(response: Response): Promise<ApiError> {
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(
    await parseApiError(response),
    response.status,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

// The session is an httpOnly cookie set by the API, so every call must send credentials.
function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  return fetch(`${API_URL}${path}`, { ...options, credentials: "include" });
}

export async function adminLogin(email: string, password: string): Promise<AdminUser> {
  const response = await apiFetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json();
}

export async function adminLogout(): Promise<void> {
  await apiFetch("/api/v1/auth/logout", { method: "POST" });
}

/** The signed-in user, or null when there is no valid session. */
export async function getCurrentUser(): Promise<AdminUser | null> {
  const response = await apiFetch("/api/v1/auth/me");
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json();
}

/** For admin pages. A 401 means the session ended, so go to the login page. */
export async function adminFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const response = await apiFetch(path, options);
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.assign(`${LOGIN_PATH}?expired=1`);
  }
  return response;
}
