const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type SourceCitation = {
  filename: string;
  excerpt: string;
  score?: number | null;
};

export type ChatResponse = {
  reply: string;
  thread_id: string;
  sources: SourceCitation[];
  action?: string | null;
  safety?: "urgent" | "diagnosis" | null;
  session_id?: string | null;
  /** True when an earlier conversation ended (30 minutes idle or closed) and this one started. */
  session_reset?: boolean;
  answer_source?: "model" | "faq" | "fallback" | "safety" | "booking" | "live" | null;
  /** Contact options when the knowledge base could not answer. Labels are already localized. */
  fallback?: FallbackCard | null;
};

export type FallbackCard = {
  labels: { call: string; text: string; callback: string; live_chat: string };
  /** tel: link, present only when the practice has a number. */
  call: string | null;
  /** sms: link, present only when the practice has a number. */
  text: string | null;
  callback: boolean;
  live_chat: boolean;
};

/** The widget's client is unknown or suspended (the API answers 404). */
export class ChatUnavailableError extends Error {
  constructor() {
    super("This assistant is not available");
  }
}

/** Any other non-2xx answer from the API, with its status and the API's own message. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// Mirrors SLUG_PATTERN in backend/app/schemas/users.py.
const CLIENT_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** The client slug from the page URL (?client=...), or null if missing or malformed. */
export function clientSlugFromUrl(search: string): string | null {
  const slug = new URLSearchParams(search).get("client")?.trim().toLowerCase() ?? "";
  return slug.length >= 2 && slug.length <= 100 && CLIENT_SLUG_PATTERN.test(slug) ? slug : null;
}

async function errorDetail(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 404) {
    throw new ChatUnavailableError();
  }
  if (!response.ok) {
    throw new ApiRequestError(await errorDetail(response), response.status);
  }
  return response.json();
}

export async function sendMessage(
  message: string,
  client: string,
  threadId?: string | null,
): Promise<ChatResponse> {
  return postJson<ChatResponse>("/api/v1/chat", {
    message,
    client,
    thread_id: threadId ?? null,
  });
}

export type WidgetConfig = {
  client: string;
  /** The client's display name, for the header and question buttons. */
  name: string;
  /** The client's default questions, in display order (may be empty). */
  default_questions: string[];
};

/** The widget's settings for this client; ChatUnavailableError if unknown or suspended. */
export async function getWidgetConfig(client: string): Promise<WidgetConfig> {
  const response = await fetch(
    `${API_URL}/api/v1/widget/config?client=${encodeURIComponent(client)}`,
  );
  if (response.status === 404) {
    throw new ChatUnavailableError();
  }
  if (!response.ok) {
    throw new Error(`Widget config request failed (${response.status})`);
  }
  return response.json();
}

// ---------------------------------------------------------------- conversation sessions

export type SessionStart = { thread_id: string; session_id: string };

/** Opens a conversation when the visitor makes the welcome choice. */
export function startSession(client: string): Promise<SessionStart> {
  return postJson<SessionStart>("/api/v1/chat/session/start", { client });
}

export type SessionStatus = { active: boolean; session_id?: string | null; known: boolean };

/** Whether the stored conversation is still open. Changes nothing on the server. */
export async function getSessionStatus(client: string, threadId: string): Promise<SessionStatus> {
  const params = new URLSearchParams({ client, thread_id: threadId });
  const response = await fetch(`${API_URL}/api/v1/chat/session?${params}`);
  if (response.status === 404) {
    throw new ChatUnavailableError();
  }
  if (!response.ok) {
    throw new ApiRequestError(await errorDetail(response), response.status);
  }
  return response.json();
}

export type ConsentOffer = { purpose: string; version: number; text: string };

/** The approved wording for sharing details, or null when none is approved (no sharing). */
export async function getConsentOffer(
  client: string,
  purpose: "share_contact" | "callback" = "share_contact",
): Promise<ConsentOffer | null> {
  const params = new URLSearchParams({ client, purpose });
  const response = await fetch(`${API_URL}/api/v1/chat/consent-text?${params}`);
  if (response.status === 404) {
    return null;
  }
  if (response.status === 400 || response.status === 422) {
    return null;
  }
  if (!response.ok) {
    throw new ApiRequestError(await errorDetail(response), response.status);
  }
  return response.json();
}

export type ShareDetails = {
  client: string;
  thread_id: string;
  session_id: string;
  purpose: "share_contact" | "callback";
  text_version: number;
  full_name: string;
  phone: string;
  is_current_patient: boolean;
  consent_accepted: boolean;
};

/** Stores the visitor's name and phone with their consent. Throws ApiRequestError on refusal. */
export function submitShareConsent(details: ShareDetails): Promise<{ known: boolean }> {
  return postJson<{ known: boolean }>("/api/v1/chat/session/consent", details);
}

/**
 * Tells the API the widget page is closing. Uses keepalive so the request can finish after the
 * page has gone. Errors are ignored: the server ends idle sessions on its own.
 */
export function closeSession(client: string, threadId: string): void {
  try {
    void fetch(`${API_URL}/api/v1/chat/session/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client, thread_id: threadId }),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // The page is going away; nothing to report.
  }
}
