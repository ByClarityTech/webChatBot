import { adminFetch } from "@/lib/admin-api";

const BASE = "/api/v1/admin/inbox";

export type InboxContact = { name: string | null; phone: string | null; is_current_patient?: boolean } | null;

export type PendingItem = {
  id: string;
  session_id: string;
  requested_at: string;
  seconds_left: number;
  contact: InboxContact;
};

export type ActiveItem = {
  id: string;
  session_id: string;
  accepted_by: string | null;
  accepted_at: string | null;
};

export type CallbackItem = {
  id: string;
  source: string;
  created_at: string;
  session_id: string | null;
  view_chat: string | null;
  contact: InboxContact;
};

export type InboxQueues = {
  online: number;
  pending: PendingItem[];
  active: ActiveItem[];
  callbacks: CallbackItem[];
};

export type ConversationMessage = {
  id: string;
  sender: "visitor" | "staff";
  body: string;
  created_at: string;
};

export type Conversation = {
  id: string;
  status: string;
  accepted_by: string | null;
  expires_at: string;
  seconds_left: number;
  is_test_session: boolean;
  contact: InboxContact;
  messages: ConversationMessage[];
};

/** Pulls the API's own message out of an error response. */
export async function failure(response: Response): Promise<Error> {
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

export const queues = (withClient: (p: string) => string) =>
  call<InboxQueues>(withClient, BASE);

export const conversation = (withClient: (p: string) => string, id: string) =>
  call<Conversation>(withClient, `${BASE}/${encodeURIComponent(id)}`);

export const acceptChat = (withClient: (p: string) => string, id: string) =>
  call<{ id: string; status: string }>(withClient, `${BASE}/${encodeURIComponent(id)}/accept`, "POST");

export const sendReply = (withClient: (p: string) => string, id: string, body: string) =>
  call<{ id: string }>(withClient, `${BASE}/${encodeURIComponent(id)}/messages`, "POST", { body });

export const endChat = (withClient: (p: string) => string, id: string) =>
  call<void>(withClient, `${BASE}/${encodeURIComponent(id)}/end`, "POST");

export const requestDraft = (withClient: (p: string) => string, id: string) =>
  call<{ draft: string }>(withClient, `${BASE}/${encodeURIComponent(id)}/draft`, "POST");

export const saveDraftFaq = (withClient: (p: string) => string, id: string, answer: string) =>
  call<{ id: string }>(withClient, `${BASE}/${encodeURIComponent(id)}/save-faq`, "POST", { answer });

export const heartbeat = (withClient: (p: string) => string, tabId: string) =>
  call<{ online: number }>(withClient, `${BASE}/presence`, "POST", { tab_id: tabId });

export const handleCallback = (withClient: (p: string) => string, id: string) =>
  call<{ status: string }>(withClient, `/api/v1/admin/callbacks/${encodeURIComponent(id)}/handle`, "POST");

/** The live event stream for this client's staff. Read with consumeSse. */
export function openStaffStream(withClient: (p: string) => string, signal: AbortSignal): Promise<Response> {
  return adminFetch(withClient(`${BASE}/stream`), { signal });
}
