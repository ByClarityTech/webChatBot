// Admin API for analytics, conversations, and the zero-PHI export. Mirrors
// backend/app/schemas/analytics.py. Kept apart from admin-api.ts.

import { ApiError, adminFetch } from "@/lib/admin-api";

export type OutcomeKey = "booking" | "callback" | "handover" | "resolved" | "abandoned";
export type RangePreset = "7d" | "30d" | "90d";

/** Which period and client to ask for. A custom range sends both dates, not a preset. */
export type RangeQuery = {
  preset?: RangePreset;
  start?: string;
  end?: string;
  clientId?: string;
};

export type Period = { start: string; end: string; timezone: string; days: number };

export type Summary = {
  started: number;
  ended: number;
  in_progress: number;
  engaged: number;
  bounced: number;
  booking_rate: number | null;
  callback_rate: number | null;
  handover_rate: number | null;
  resolution_rate: number | null;
  abandonment_rate: number | null;
  average_duration_seconds: number | null;
  handovers_created: number;
  handovers_accepted: number;
  handovers_timed_out: number;
  handover_accept_rate: number | null;
  handover_timeout_rate: number | null;
};

export type FunnelStep = {
  key: string;
  label: string;
  count: number;
  percent_of_engaged: number | null;
};

export type OutcomeCount = {
  outcome: OutcomeKey;
  label: string;
  count: number;
  percent_of_engaged: number | null;
};

export type DayPoint = {
  day: string;
  started: number;
  engaged: number;
  booking: number;
  callback: number;
  handover: number;
  resolved: number;
  abandoned: number;
};

export type TopGap = {
  gap_id: string;
  question: string;
  reason: string;
  asks: number;
  last_asked_at: string;
};

export type AnalyticsReport = {
  client_id: string | null;
  period: Period;
  summary: Summary;
  funnel: FunnelStep[];
  outcomes: OutcomeCount[];
  daily: DayPoint[];
  top_gaps: TopGap[];
  note: string;
};

export type ConversationItem = {
  session_id: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  status: "in_progress" | "ended";
  conversation_type: "anonymous" | "known";
  outcome: OutcomeKey | null;
  outcome_label: string | null;
  reason: string | null;
  engaged: boolean | null;
  visitor_messages: number | null;
  bot_replies: number | null;
  unanswered_questions: number | null;
};

export type ConversationList = {
  items: ConversationItem[];
  total: number;
  limit: number;
  offset: number;
};

export type PatientGroup = {
  patient_key: string;
  masked_phone: string | null;
  name: string | null;
  conversations: number;
  first_seen_at: string;
  last_seen_at: string;
  latest_session_id: string | null;
};

export type PatientGroupList = {
  groups: PatientGroup[];
  total: number;
  limit: number;
  offset: number;
};

export type TranscriptLine = { position: number; role: "visitor" | "assistant"; text: string };
export type StaffMessage = { sender: "visitor" | "staff"; body: string; created_at: string };

export type ConversationDetail = {
  session_id: string;
  started_at: string;
  ended_at: string | null;
  status: "in_progress" | "ended";
  conversation_type: "anonymous" | "known";
  outcome: OutcomeKey | null;
  outcome_label: string | null;
  reason: string | null;
  engaged: boolean | null;
  visitor_messages: number | null;
  bot_replies: number | null;
  duration_seconds: number | null;
  unanswered: { question: string; asked_at: string }[];
  contact: { name: string; masked_phone: string | null } | null;
  transcript: TranscriptLine[];
  staff_messages: StaffMessage[];
  notice: string;
};

export type ConversationQuery = RangeQuery & {
  outcome?: OutcomeKey | "in_progress";
  phone?: string;
  limit?: number;
  offset?: number;
};

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function rangeParams(range: RangeQuery): Record<string, string | undefined> {
  return {
    preset: range.preset,
    start: range.start,
    end: range.end,
    client_id: range.clientId,
  };
}

async function toApiError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status})`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      message = body.detail;
    }
  } catch {
    // Not JSON: keep the generic message.
  }
  return new ApiError(message, response.status);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await adminFetch(path);
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function getAnalytics(range: RangeQuery): Promise<AnalyticsReport> {
  return getJson(`/api/v1/admin/analytics${query(rangeParams(range))}`);
}

export function listConversations(options: ConversationQuery): Promise<ConversationList> {
  return getJson(
    `/api/v1/admin/conversations${query({
      ...rangeParams(options),
      outcome: options.outcome,
      phone: options.phone,
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export function listPatientGroups(options: RangeQuery & { limit?: number; offset?: number }) {
  return getJson<PatientGroupList>(
    `/api/v1/admin/conversations/patients${query({
      ...rangeParams(options),
      limit: options.limit,
      offset: options.offset,
    })}`,
  );
}

export function getConversation(sessionId: string, clientId?: string): Promise<ConversationDetail> {
  return getJson(
    `/api/v1/admin/conversations/${encodeURIComponent(sessionId)}${query({ client_id: clientId })}`,
  );
}

/** Downloads a CSV export. The file is saved by the browser; nothing is kept in the page. */
export async function downloadExport(
  kind: "summary" | "conversations",
  range: RangeQuery,
): Promise<void> {
  const response = await adminFetch(
    `/api/v1/admin/analytics/export${query({ kind, ...rangeParams(range) })}`,
  );
  if (!response.ok) {
    throw await toApiError(response);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${kind}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
