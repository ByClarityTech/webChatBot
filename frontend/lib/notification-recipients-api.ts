import { adminFetch } from "@/lib/admin-api";

const BASE = "/api/v1/admin/settings/notification-recipients";

export type NotificationRecipient = {
  id: string;
  email: string;
  enabled: boolean;
  created_at: string;
};

export type NotificationRecipientList = {
  recipients: NotificationRecipient[];
  limit: number;
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
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const listNotificationRecipients = (withClient: (p: string) => string) =>
  call<NotificationRecipientList>(withClient, BASE);

export const addNotificationRecipient = (withClient: (p: string) => string, email: string) =>
  call<NotificationRecipient>(withClient, BASE, "POST", { email });

export const setNotificationRecipientEnabled = (
  withClient: (p: string) => string,
  id: string,
  enabled: boolean,
) =>
  call<NotificationRecipient>(withClient, `${BASE}/${encodeURIComponent(id)}`, "PATCH", {
    enabled,
  });

export const removeNotificationRecipient = (withClient: (p: string) => string, id: string) =>
  call<void>(withClient, `${BASE}/${encodeURIComponent(id)}`, "DELETE");
