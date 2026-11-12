import { adminFetch } from "@/lib/admin-api";

const API_PREFIX = "/api/v1/admin/faq-gaps";

export type FaqGap = {
  id: string;
  /** The visitor's wording. Shown to admins only. */
  question: string;
  reason: "no_match" | "model_declined" | "keyword_miss";
  top_score: number | null;
  ask_count: number;
  status: "open" | "drafted" | "dismissed";
  drafted_faq_id: string | null;
  last_seen_at: string;
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

/** Open unanswered questions, most asked first. `withClient` adds ?client_id= for Super Admins. */
export async function listGaps(withClient: (path: string) => string): Promise<FaqGap[]> {
  const response = await adminFetch(withClient(API_PREFIX));
  if (!response.ok) {
    throw await failure(response);
  }
  return ((await response.json()) as { gaps: FaqGap[] }).gaps;
}

/** Creates a draft FAQ from the question. The answer is written by a person, then approved. */
export async function draftGap(withClient: (path: string) => string, gapId: string): Promise<void> {
  const response = await adminFetch(
    withClient(`${API_PREFIX}/${encodeURIComponent(gapId)}/draft`),
    { method: "POST" },
  );
  if (!response.ok) {
    throw await failure(response);
  }
}

export async function dismissGap(withClient: (path: string) => string, gapId: string): Promise<void> {
  const response = await adminFetch(
    withClient(`${API_PREFIX}/${encodeURIComponent(gapId)}/dismiss`),
    { method: "POST" },
  );
  if (!response.ok) {
    throw await failure(response);
  }
}
