"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/admin-api";
import {
  CrawledPage,
  CrawlRun,
  listCrawledPages,
  listCrawlRuns,
  setPageIncluded,
} from "@/lib/admin-sources-api";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

/** What the chatbot is doing with a page. Text labels, so colour is not the only cue. */
export function pageStatus(page: CrawledPage): string {
  if (page.live) {
    return "Live";
  }
  if (page.status === "missing") {
    return "Removed";
  }
  return "Not in chatbot";
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "Never";
}

export function CrawledPagesPanel({ clientId, refreshKey }: { clientId?: string; refreshKey: number }) {
  const [pages, setPages] = useState<CrawledPage[] | null>(null);
  const [runs, setRuns] = useState<CrawlRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [loadedPages, loadedRuns] = await Promise.all([
      listCrawledPages(clientId),
      listCrawlRuns(clientId),
    ]);
    setPages(loadedPages);
    setRuns(loadedRuns);
  }, [clientId]);

  useEffect(() => {
    setError(null);
    load().catch((err) => setError(errorMessage(err, "Could not load crawled pages.")));
  }, [load, refreshKey]);

  async function toggle(page: CrawledPage) {
    setSavingId(page.id);
    setError(null);
    try {
      await setPageIncluded(page.id, !page.included, clientId);
      await load();
    } catch (err) {
      setError(errorMessage(err, "The page could not be changed."));
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="space-y-8">
      {error ? (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">
          {error}
        </p>
      ) : null}

      <section aria-labelledby="pages-heading">
        <h2 id="pages-heading" className="text-base font-semibold text-slate-900">
          Crawled pages
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Excluding a page the chatbot uses proposes its removal. Nothing is removed until you
          approve it under Pending changes.
        </p>
        {pages === null ? (
          <p className="mt-3 text-sm text-slate-500">Loading pages...</p>
        ) : pages.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600">No pages yet. Run a crawl to find them.</p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-left text-sm">
              <caption className="sr-only">Pages found on the website</caption>
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th scope="col" className="px-4 py-2">Page</th>
                  <th scope="col" className="px-4 py-2">Status</th>
                  <th scope="col" className="px-4 py-2">HTTP</th>
                  <th scope="col" className="px-4 py-2">Last crawled</th>
                  <th scope="col" className="px-4 py-2">Include</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {pages.map((page) => (
                  <tr key={page.id}>
                    <td className="max-w-xs px-4 py-2">
                      <span className="block truncate font-medium text-slate-900">{page.url}</span>
                      {page.title ? (
                        <span className="block truncate text-xs text-slate-500">{page.title}</span>
                      ) : null}
                      {page.last_error ? (
                        <span className="block text-xs text-rose-800">{page.last_error}</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2 text-slate-700">{pageStatus(page)}</td>
                    <td className="px-4 py-2 font-mono text-xs text-slate-700">
                      {page.last_status_code ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-slate-600">{formatTime(page.last_crawled_at)}</td>
                    <td className="px-4 py-2">
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={page.included}
                          disabled={savingId === page.id}
                          onChange={() => void toggle(page)}
                          aria-label={`Include ${page.url} in the chatbot`}
                          className="h-4 w-4"
                        />
                        <span className="text-slate-700">
                          {page.included ? "Included" : "Excluded"}
                        </span>
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="history-heading">
        <h2 id="history-heading" className="text-base font-semibold text-slate-900">
          Crawl history
        </h2>
        {runs === null ? (
          <p className="mt-3 text-sm text-slate-500">Loading history...</p>
        ) : runs.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600">No crawls have run yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-left text-sm">
              <caption className="sr-only">Past and current crawls</caption>
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th scope="col" className="px-4 py-2">Started</th>
                  <th scope="col" className="px-4 py-2">Trigger</th>
                  <th scope="col" className="px-4 py-2">Status</th>
                  <th scope="col" className="px-4 py-2">Fetched</th>
                  <th scope="col" className="px-4 py-2">Changes</th>
                  <th scope="col" className="px-4 py-2">Failed</th>
                  <th scope="col" className="px-4 py-2">Note</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="px-4 py-2 text-slate-700">{formatTime(run.started_at)}</td>
                    <td className="px-4 py-2 text-slate-700">
                      {run.trigger === "manual" ? "Manual" : "Monthly"}
                    </td>
                    <td className="px-4 py-2 text-slate-700">
                      {run.status === "running"
                        ? "Running"
                        : run.status === "completed"
                          ? "Completed"
                          : "Failed"}
                    </td>
                    <td className="px-4 py-2 tabular-nums text-slate-700">{run.pages_fetched}</td>
                    <td className="px-4 py-2 tabular-nums text-slate-700">{run.pages_changed}</td>
                    <td className="px-4 py-2 tabular-nums text-slate-700">{run.pages_failed}</td>
                    <td className="max-w-xs px-4 py-2 text-xs text-rose-800">{run.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
