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
  /** An admin set this user's password; they must choose their own before anything else. */
  must_change_password: boolean;
};

/** A user as listed in user management (GET /api/v1/admin/users). */
export type ManagedUser = {
  id: string;
  email: string;
  full_name: string;
  user_type: UserType;
  client_id: string | null;
  client_name: string | null;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
};

export type ClientStatus = "active" | "suspended";

export type ManagedClient = {
  id: string;
  name: string;
  slug: string;
  status: ClientStatus;
  user_count: number;
  created_at: string;
  updated_at: string;
};

export type DocumentStatus = "processing" | "ready" | "failed";

/** One uploaded file of a client (GET /api/v1/admin/documents). */
export type AdminDocument = {
  id: string;
  client_id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  status: DocumentStatus;
  chunk_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type AdminDocumentList = {
  client_id: string;
  documents: AdminDocument[];
  total_chunks: number;
};

export type AuditEntry = {
  id: number;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  client_ip: string | null;
  created_at: string;
  user_id: string | null;
  user_email: string | null;
  client_id: string | null;
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

/** adminFetch + JSON body in and out; any non-2xx becomes an ApiError with the API's message. */
async function adminJson<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await adminFetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------- own account

export async function changeOwnPassword(currentPassword: string, newPassword: string): Promise<void> {
  await adminJson("/api/v1/auth/password", "POST", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

// ---------------------------------------------------------------- users

export async function listUsers(clientId?: string): Promise<ManagedUser[]> {
  const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  return (await adminJson<{ users: ManagedUser[] }>(`/api/v1/admin/users${query}`)).users;
}

export type NewUser = {
  email: string;
  full_name: string;
  password: string;
  user_type: "admin" | "support";
  /** Required for Super Admins; ignored for Admins (always their own client). */
  client_id?: string;
};

export function createUser(user: NewUser): Promise<ManagedUser> {
  return adminJson<ManagedUser>("/api/v1/admin/users", "POST", user);
}

export type UserChanges = {
  full_name?: string;
  is_active?: boolean;
  password?: string;
};

export function updateUser(userId: string, changes: UserChanges): Promise<ManagedUser> {
  return adminJson<ManagedUser>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, "PATCH", changes);
}

// ---------------------------------------------------------------- clients (Super Admin)

export async function listClients(): Promise<ManagedClient[]> {
  return (await adminJson<{ clients: ManagedClient[] }>("/api/v1/admin/clients")).clients;
}

export function createClient(name: string, slug: string): Promise<ManagedClient> {
  return adminJson<ManagedClient>("/api/v1/admin/clients", "POST", { name, slug });
}

export function updateClient(
  clientId: string,
  changes: { name?: string; status?: ClientStatus },
): Promise<ManagedClient> {
  return adminJson<ManagedClient>(
    `/api/v1/admin/clients/${encodeURIComponent(clientId)}`,
    "PATCH",
    changes,
  );
}

// ---------------------------------------------------------------- documents
// Super Admins pass the client; Admins leave it out (always their own client).

function withClient(path: string, clientId?: string): string {
  return clientId ? `${path}?client_id=${encodeURIComponent(clientId)}` : path;
}

export function listDocuments(clientId?: string): Promise<AdminDocumentList> {
  return adminJson<AdminDocumentList>(withClient("/api/v1/admin/documents", clientId));
}

export async function uploadDocument(file: File, clientId?: string): Promise<AdminDocument> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await adminFetch(withClient("/api/v1/admin/documents/upload", clientId), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return response.json() as Promise<AdminDocument>;
}

export async function deleteDocument(documentId: string, clientId?: string): Promise<void> {
  await adminJson(
    withClient(`/api/v1/admin/documents/${encodeURIComponent(documentId)}`, clientId),
    "DELETE",
  );
}

export function reindexDocument(documentId: string, clientId?: string): Promise<AdminDocument> {
  return adminJson<AdminDocument>(
    withClient(`/api/v1/admin/documents/${encodeURIComponent(documentId)}/reindex`, clientId),
    "POST",
  );
}

// ---------------------------------------------------------------- audit log

export async function listAuditLog(options: { limit?: number; clientId?: string } = {}): Promise<AuditEntry[]> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 100) });
  if (options.clientId) {
    params.set("client_id", options.clientId);
  }
  return (await adminJson<{ entries: AuditEntry[] }>(`/api/v1/admin/audit-log?${params}`)).entries;
}
