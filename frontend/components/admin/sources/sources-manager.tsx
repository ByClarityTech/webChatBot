"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { ApiError } from "@/lib/admin-api";
import {
  CrawlRun,
  getSourcesOverview,
  saveWebsiteUrl,
  SourcesOverview,
  startCrawl,
} from "@/lib/admin-sources-api";

import { CrawledPagesPanel } from "./crawled-pages-panel";
import { PendingChangesPanel } from "./pending-changes-panel";

const POLL_MS = 4000;

type Tab = "pending" | "pages";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

function runSummary(run: CrawlRun | null): string {
  if (!run) {
    return "No crawl yet.";
  }
  const when = new Date(run.started_at).toLocaleString();
  if (run.status === "running") {
    return `Running since ${when}.`;
  }
  if (run.status === "failed") {
    return `Failed on ${when}. ${run.error ?? ""}`.trim();
  }
  return `Completed ${when}: ${run.pages_fetched} pages read, ${run.pages_changed} changes proposed.`;
}

function nextRunText(overview: SourcesOverview): string {
  if (!overview.website_url) {
    return "Set a website address to schedule crawls.";
  }
  if (overview.next_scheduled_at) {
    return `Next monthly crawl on ${new Date(overview.next_scheduled_at).toLocaleDateString()}.`;
  }
  return "The first monthly crawl runs at the next scheduled check.";
}

export function SourcesManager() {
  const scope = useClientScope();
  const { needsClient, scope: clientId } = scope;

  const [overview, setOverview] = useState<SourcesOverview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("pending");
  const [refreshKey, setRefreshKey] = useState(0);

  const [urlDraft, setUrlDraft] = useState("");
  // A ref, not state: reloads must not change when the draft is typed into.
  const urlEditedRef = useRef(false);
  const [savingUrl, setSavingUrl] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [urlSaved, setUrlSaved] = useState<string | null>(null);

  const [starting, setStarting] = useState(false);
  const [crawlError, setCrawlError] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    if (needsClient) {
      setOverview(null);
      return;
    }
    const loaded = await getSourcesOverview(clientId);
    setOverview(loaded);
    if (!urlEditedRef.current) {
      setUrlDraft(loaded.website_url ?? "");
    }
  }, [needsClient, clientId]);

  useEffect(() => {
    setLoadError(null);
    loadOverview().catch((err) => setLoadError(errorMessage(err, "Could not load Sources.")));
  }, [loadOverview]);

  // While a crawl runs, keep the header and the lists current.
  const running = overview?.running != null;
  useEffect(() => {
    if (!running) {
      return;
    }
    const timer = setInterval(() => {
      loadOverview()
        .then(() => setRefreshKey((key) => key + 1))
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, loadOverview]);

  async function handleSaveUrl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingUrl(true);
    setUrlError(null);
    setUrlSaved(null);
    try {
      const saved = await saveWebsiteUrl(urlDraft, clientId);
      setOverview(saved);
      setUrlDraft(saved.website_url ?? "");
      urlEditedRef.current = false;
      setUrlSaved(
        saved.website_url
          ? `Saved. Crawls stay on ${saved.website_url.replace("https://", "")} and its www. address.`
          : "Website address removed. Crawls are off until you set one.",
      );
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setUrlError(errorMessage(err, "The website address could not be saved."));
    } finally {
      setSavingUrl(false);
    }
  }

  async function handleCrawlNow() {
    setStarting(true);
    setCrawlError(null);
    try {
      await startCrawl(clientId);
      await loadOverview();
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setCrawlError(errorMessage(err, "The crawl could not be started."));
    } finally {
      setStarting(false);
    }
  }

  if (needsClient) {
    return (
      <div className="space-y-4">
        <ClientPicker scope={scope} id="sources-client" />
        <p className="text-sm text-slate-600">Choose a client to see its website sources.</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">
        {loadError}
      </p>
    );
  }

  if (!overview) {
    return <p className="text-sm text-slate-500">Loading Sources...</p>;
  }

  const crawlDisabled = starting || running || !overview.website_url;

  return (
    <div className="space-y-8">
      <ClientPicker scope={scope} id="sources-client" />

      <section aria-labelledby="website-heading" className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 id="website-heading" className="text-base font-semibold text-slate-900">
          Practice website
        </h2>
        <form onSubmit={(event) => void handleSaveUrl(event)} className="mt-3 flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor="website-url" className="block text-xs font-medium text-slate-600">
              Website address
            </label>
            <input
              id="website-url"
              type="text"
              inputMode="url"
              autoComplete="url"
              placeholder="https://clinic.example"
              value={urlDraft}
              onChange={(event) => {
                setUrlDraft(event.target.value);
                urlEditedRef.current = true;
              }}
              aria-describedby="website-url-help"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <p id="website-url-help" className="mt-1 text-xs text-slate-500">
              The site address only, such as https://clinic.example. Crawls stay on this site.
            </p>
          </div>
          <button
            type="submit"
            disabled={savingUrl}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {savingUrl ? "Saving..." : "Save address"}
          </button>
        </form>
        {urlError ? (
          <p role="alert" className="mt-3 text-sm text-rose-900">
            {urlError}
          </p>
        ) : null}
        {urlSaved ? (
          <p role="status" className="mt-3 text-sm text-emerald-900">
            {urlSaved}
          </p>
        ) : null}

        <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs font-medium text-slate-600">Last crawl</dt>
            <dd className="mt-1 text-slate-900">{runSummary(overview.last_run)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-slate-600">Monthly schedule</dt>
            <dd className="mt-1 text-slate-900">{nextRunText(overview)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-slate-600">Limits</dt>
            <dd className="mt-1 text-slate-900">
              Up to {overview.max_pages} pages, {overview.max_depth} levels from the home page, one
              page at a time.
            </dd>
          </div>
        </dl>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void handleCrawlNow()}
            disabled={crawlDisabled}
            aria-describedby={!overview.website_url ? "crawl-help" : undefined}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? "Crawling..." : starting ? "Starting..." : "Crawl now"}
          </button>
          {!overview.website_url ? (
            <span id="crawl-help" className="text-xs text-slate-500">
              Save a website address first.
            </span>
          ) : null}
          {crawlError ? (
            <span role="alert" className="text-sm text-rose-900">
              {crawlError}
            </span>
          ) : null}
        </div>
      </section>

      <div>
        <div role="tablist" aria-label="Sources sections" className="flex gap-1 border-b border-slate-200">
          {(
            [
              ["pending", `Pending changes (${overview.pending_count})`],
              ["pages", "Crawled pages"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              id={`tab-${key}`}
              role="tab"
              type="button"
              aria-selected={tab === key}
              aria-controls={`panel-${key}`}
              onClick={() => setTab(key)}
              className={`-mb-px rounded-t-lg border border-b-0 px-4 py-2 text-sm font-medium ${
                tab === key
                  ? "border-slate-200 bg-white text-slate-900"
                  : "border-transparent text-slate-600 hover:text-slate-900"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div id="panel-pending" role="tabpanel" aria-labelledby="tab-pending" hidden={tab !== "pending"} className="pt-6">
          {tab === "pending" ? (
            <PendingChangesPanel
              clientId={clientId}
              refreshKey={refreshKey}
              onDecided={() => {
                setRefreshKey((key) => key + 1);
                void loadOverview();
              }}
            />
          ) : null}
        </div>
        <div id="panel-pages" role="tabpanel" aria-labelledby="tab-pages" hidden={tab !== "pages"} className="pt-6">
          {tab === "pages" ? <CrawledPagesPanel clientId={clientId} refreshKey={refreshKey} /> : null}
        </div>
      </div>
    </div>
  );
}
