"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "@/lib/admin-api";

type DashboardSummary = {
  document_count: number;
  bookings_today: number;
  calendar_connected: boolean;
};

async function parseError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

function SummaryCard({
  title,
  value,
  href,
  hint,
}: {
  title: string;
  value: string;
  href?: string;
  hint?: string;
}) {
  const content = (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{title}</p>
      <p className="mt-2 text-3xl font-semibold text-slate-900">{value}</p>
      {hint ? <p className="mt-2 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block transition hover:opacity-90">
        {content}
      </Link>
    );
  }

  return content;
}

export default function AdminDashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    setError(null);
    const response = await adminFetch("/api/v1/admin/dashboard/summary");
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    return (await response.json()) as DashboardSummary;
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      try {
        const data = await loadSummary();
        if (!cancelled) {
          setSummary(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load dashboard");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [loadSummary]);

  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Admin dashboard</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Deployment overview for this instance. Use the sidebar to manage documents, calendar,
        business hours, and bookings.
      </p>

      {loading ? (
        <p className="mt-8 text-sm text-slate-500">Loading summary...</p>
      ) : error ? (
        <p className="mt-8 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : summary ? (
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <SummaryCard
            title="Indexed documents"
            value={String(summary.document_count)}
            href="/admin/documents"
            hint="Open Documents to upload or remove files"
          />
          <SummaryCard
            title="Bookings today"
            value={String(summary.bookings_today)}
            href="/admin/bookings"
            hint="Slots scheduled for today in your business timezone"
          />
          <SummaryCard
            title="Google Calendar"
            value={summary.calendar_connected ? "Connected" : "Not connected"}
            href="/admin/calendar"
            hint={
              summary.calendar_connected
                ? "Calendar linked for availability and bookings"
                : "Connect calendar to enable booking"
            }
          />
        </div>
      ) : null}

      <section className="mt-10 max-w-3xl rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Chat assistant prompts</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          System prompts live in{" "}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-800">
            backend/app/agents/public/prompts/
          </code>
          . They are code-only — redeploy the API container to change assistant behavior. This
          admin UI does not edit prompts at runtime.
        </p>
      </section>
    </div>
  );
}
