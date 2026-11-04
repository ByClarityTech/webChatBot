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
};

/** The widget's client is unknown or suspended (the API answers 404). */
export class ChatUnavailableError extends Error {
  constructor() {
    super("This assistant is not available");
  }
}

// Mirrors SLUG_PATTERN in backend/app/schemas/users.py.
const CLIENT_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** The client slug from the page URL (?client=...), or null if missing or malformed. */
export function clientSlugFromUrl(search: string): string | null {
  const slug = new URLSearchParams(search).get("client")?.trim().toLowerCase() ?? "";
  return slug.length >= 2 && slug.length <= 100 && CLIENT_SLUG_PATTERN.test(slug) ? slug : null;
}

export async function sendMessage(
  message: string,
  client: string,
  threadId?: string | null,
): Promise<ChatResponse> {
  const response = await fetch(`${API_URL}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      client,
      thread_id: threadId ?? null,
    }),
  });

  if (response.status === 404) {
    throw new ChatUnavailableError();
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Chat request failed (${response.status})`);
  }

  return response.json();
}

export type WidgetConfig = {
  client: string;
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
