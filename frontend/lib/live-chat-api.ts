const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type LiveRequestResult = {
  status: "pending" | "callback";
  handover_id: string | null;
  token: string | null;
  expires_at: string | null;
};

export type LiveMessage = {
  id: string;
  handover_id: string;
  sender: "visitor" | "staff";
  body: string;
  created_at: string;
};

export type LiveSnapshot = {
  handover: { handover_id: string; status: string; expires_at: string };
  messages: LiveMessage[];
};

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

/** Asks for a person. Pending (with the stream token) or a callback when nobody is online. */
export async function requestLive(
  client: string,
  threadId: string,
  sessionId: string,
): Promise<LiveRequestResult> {
  const response = await fetch(`${API_URL}/api/v1/chat/live/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client, thread_id: threadId, session_id: sessionId }),
  });
  if (!response.ok) {
    throw await failure(response);
  }
  return response.json();
}

/** The live stream for one handover. The token goes in a header, never in the URL. */
export function openLiveStream(
  client: string,
  handoverId: string,
  token: string,
  signal: AbortSignal,
): Promise<Response> {
  const params = new URLSearchParams({ client, handover_id: handoverId });
  return fetch(`${API_URL}/api/v1/chat/live/stream?${params}`, {
    headers: { "X-Live-Token": token },
    signal,
  });
}

export async function leaveLive(client: string, handoverId: string, token: string): Promise<void> {
  const params = new URLSearchParams({ client, handover_id: handoverId });
  await fetch(`${API_URL}/api/v1/chat/live/leave?${params}`, {
    method: "POST",
    headers: { "X-Live-Token": token },
    keepalive: true,
  }).catch(() => undefined);
}
