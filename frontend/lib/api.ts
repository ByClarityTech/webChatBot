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

export async function sendMessage(
  message: string,
  threadId?: string | null,
): Promise<ChatResponse> {
  const response = await fetch(`${API_URL}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      thread_id: threadId ?? null,
    }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Chat request failed (${response.status})`);
  }

  return response.json();
}
