const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const ADMIN_API_KEY_STORAGE = "admin-api-key";

export function getAdminApiKey(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return sessionStorage.getItem(ADMIN_API_KEY_STORAGE);
}

export function setAdminApiKey(key: string): void {
  sessionStorage.setItem(ADMIN_API_KEY_STORAGE, key);
}

export function clearAdminApiKey(): void {
  sessionStorage.removeItem(ADMIN_API_KEY_STORAGE);
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

export async function adminLogin(apiKey: string): Promise<{ ok: boolean }> {
  const response = await fetch(`${API_URL}/api/v1/admin/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return response.json();
}

export async function adminFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const apiKey = getAdminApiKey();
  const headers = new Headers(options.headers);

  if (apiKey) {
    headers.set("Authorization", `Bearer ${apiKey}`);
  }

  return fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });
}
