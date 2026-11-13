import { adminFetch } from "@/lib/admin-api";

const BASE = "/api/v1/admin/widget";

export type WidgetStatus = "live" | "paused";

export type WidgetAdmin = {
  client_id: string;
  client_slug: string;
  status: WidgetStatus;
  bot_name: string | null;
  greeting: string | null;
  header_color: string;
  header_text_color: string;
  contrast_ratio: number;
  /** Relative path on the API, or null. */
  logo_url: string | null;
  has_logo: boolean;
  public_url: string;
  snippet: string;
  updated_at: string | null;
};

export type WidgetCheckCode =
  | "OK"
  | "INVALID_URL"
  | "NOT_ALLOWED_HOST"
  | "BLOCKED_ADDRESS"
  | "FETCH_FAILED"
  | "REDIRECT_BLOCKED"
  | "TOO_LARGE"
  | "SNIPPET_NOT_FOUND"
  | "WRONG_SOURCE";

export type WidgetCheck = {
  code: WidgetCheckCode;
  message: string;
  checked_url: string;
  note: string;
};

/** Pulls the API's own message out of an error response. */
async function failure(response: Response): Promise<Error> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return new Error(body.detail);
    }
  } catch {
    // not JSON
  }
  return new Error(text || `Request failed (${response.status})`);
}

async function call<T>(
  withClient: (path: string) => string,
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await adminFetch(withClient(path), {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw await failure(response);
  }
  return (await response.json()) as T;
}

export const getWidgetAdmin = (withClient: (p: string) => string) =>
  call<WidgetAdmin>(withClient, BASE);

export const setWidgetStatus = (withClient: (p: string) => string, status: WidgetStatus) =>
  call<WidgetAdmin>(withClient, `${BASE}/status`, "PUT", { status });

export const saveWidgetBranding = (
  withClient: (p: string) => string,
  values: { bot_name: string; greeting: string; header_color: string },
) => call<WidgetAdmin>(withClient, `${BASE}/branding`, "PUT", values);

export async function uploadWidgetLogo(
  withClient: (p: string) => string,
  file: File,
): Promise<WidgetAdmin> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await adminFetch(withClient(`${BASE}/logo`), {
    method: "PUT",
    body: formData,
  });
  if (!response.ok) {
    throw await failure(response);
  }
  return (await response.json()) as WidgetAdmin;
}

export const removeWidgetLogo = (withClient: (p: string) => string) =>
  call<WidgetAdmin>(withClient, `${BASE}/logo`, "DELETE");

export const verifyWidgetInstallation = (withClient: (p: string) => string, url: string) =>
  call<WidgetCheck>(withClient, `${BASE}/verify`, "POST", { url });
