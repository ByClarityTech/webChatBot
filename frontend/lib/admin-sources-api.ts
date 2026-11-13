// Admin API for the Sources page (website crawler). Mirrors backend/app/schemas/crawler.py.
// Kept apart from admin-api.ts so the crawler work does not touch that file.

import { ApiError, adminFetch } from "@/lib/admin-api";

export type CrawlRun = {
  id: string;
  trigger: "scheduled" | "manual";
  status: "running" | "completed" | "failed";
  pages_fetched: number;
  pages_changed: number;
  pages_failed: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type SourcesOverview = {
  client_id: string;
  website_url: string | null;
  running: CrawlRun | null;
  last_run: CrawlRun | null;
  next_scheduled_at: string | null;
  interval_days: number;
  pending_count: number;
  max_pages: number;
  max_depth: number;
};

export type CrawledPage = {
  id: string;
  url: string;
  title: string | null;
  included: boolean;
  status: "active" | "missing";
  live: boolean;
  last_status_code: number | null;
  last_error: string | null;
  last_crawled_at: string | null;
};

export type ChangeType = "new" | "changed" | "removed";

export type PendingChange = {
  id: string;
  page_id: string;
  page_url: string;
  change_type: ChangeType;
  status: "pending" | "approved" | "rejected" | "superseded";
  proposed_title: string | null;
  words_added: number;
  words_removed: number;
  created_at: string;
  decided_at: string | null;
};

export type DiffSegment = { op: "equal" | "insert" | "delete"; text: string };

export type PendingChangeDetail = PendingChange & {
  live_title: string | null;
  segments: DiffSegment[];
};

function withClient(path: string, clientId?: string): string {
  if (!clientId) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}client_id=${encodeURIComponent(clientId)}`;
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
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(
    message,
    response.status,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await adminFetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function getSourcesOverview(clientId?: string): Promise<SourcesOverview> {
  return request(withClient("/api/v1/admin/sources/overview", clientId));
}

export function saveWebsiteUrl(websiteUrl: string, clientId?: string): Promise<SourcesOverview> {
  return request(withClient("/api/v1/admin/sources/website-url", clientId), "PUT", {
    website_url: websiteUrl.trim() === "" ? null : websiteUrl,
  });
}

export function startCrawl(clientId?: string): Promise<CrawlRun> {
  return request(withClient("/api/v1/admin/sources/crawls", clientId), "POST");
}

export async function listCrawlRuns(clientId?: string): Promise<CrawlRun[]> {
  const body = await request<{ runs: CrawlRun[] }>(
    withClient("/api/v1/admin/sources/crawls", clientId),
  );
  return body.runs;
}

export async function listCrawledPages(clientId?: string): Promise<CrawledPage[]> {
  const body = await request<{ pages: CrawledPage[] }>(
    withClient("/api/v1/admin/sources/pages", clientId),
  );
  return body.pages;
}

export function setPageIncluded(
  pageId: string,
  included: boolean,
  clientId?: string,
): Promise<CrawledPage> {
  return request(
    withClient(`/api/v1/admin/sources/pages/${encodeURIComponent(pageId)}`, clientId),
    "PATCH",
    { included },
  );
}

export async function listPendingChanges(clientId?: string): Promise<PendingChange[]> {
  const body = await request<{ changes: PendingChange[] }>(
    withClient("/api/v1/admin/sources/changes", clientId),
  );
  return body.changes;
}

export function getPendingChange(updateId: string, clientId?: string): Promise<PendingChangeDetail> {
  return request(
    withClient(`/api/v1/admin/sources/changes/${encodeURIComponent(updateId)}`, clientId),
  );
}

export function approveChange(updateId: string, clientId?: string): Promise<PendingChange> {
  return request(
    withClient(`/api/v1/admin/sources/changes/${encodeURIComponent(updateId)}/approve`, clientId),
    "POST",
  );
}

export function rejectChange(updateId: string, clientId?: string): Promise<PendingChange> {
  return request(
    withClient(`/api/v1/admin/sources/changes/${encodeURIComponent(updateId)}/reject`, clientId),
    "POST",
  );
}
