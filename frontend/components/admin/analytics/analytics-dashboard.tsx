"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import {
  AnalyticsReport,
  DayPoint,
  downloadExport,
  getAnalytics,
  OutcomeKey,
  RangePreset,
  RangeQuery,
} from "@/lib/admin-analytics-api";
import { ApiError, listClients, ManagedClient } from "@/lib/admin-api";

const MAX_RANGE_DAYS = 366;

// Colour-blind safe palette. Each outcome also has a text label in the table and legend.
const OUTCOME_COLOURS: Record<OutcomeKey, string> = {
  booking: "#0072B2",
  callback: "#E69F00",
  handover: "#009E73",
  resolved: "#56B4E9",
  abandoned: "#7F7F7F",
};
const OUTCOME_ORDER: OutcomeKey[] = ["booking", "callback", "handover", "resolved", "abandoned"];
const OUTCOME_NAMES: Record<OutcomeKey, string> = {
  booking: "Booking",
  callback: "Callback",
  handover: "Handover",
  resolved: "Resolved",
  abandoned: "Abandoned",
};

const selectClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm";
const buttonClass =
  "rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

export function percent(value: number | null): string {
  return value === null ? "No data yet" : `${(value * 100).toFixed(1)}%`;
}

export function durationText(seconds: number | null): string {
  if (seconds === null) {
    return "No data yet";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return minutes > 0 ? `${minutes} min ${rest} s` : `${rest} s`;
}

/** Days in the range, or null when the custom dates are not a valid range. */
export function customRangeProblem(start: string, end: string): string | null {
  if (!start || !end) {
    return "Choose both a start and an end date.";
  }
  const days = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
  if (days < 1) {
    return "The end date is before the start date.";
  }
  if (days > MAX_RANGE_DAYS) {
    return `A range can be at most ${MAX_RANGE_DAYS} days.`;
  }
  return null;
}

function KpiCard({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium text-slate-600">{title}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

function DailyChart({ points }: { points: DayPoint[] }) {
  const max = Math.max(1, ...points.map((point) => point.engaged));
  const width = Math.max(320, points.length * 18);
  const height = 160;
  const barWidth = width / Math.max(1, points.length);

  return (
    <div className="overflow-x-auto">
      <svg
        role="img"
        aria-label="Engaged conversations per day, stacked by outcome. The table below has the same numbers."
        viewBox={`0 0 ${width} ${height + 20}`}
        className="h-44 w-full min-w-[320px]"
      >
        {points.map((point, index) => {
          let offset = 0;
          return (
            <g key={point.day}>
              <title>{`${point.day}: ${point.engaged} engaged`}</title>
              {OUTCOME_ORDER.map((outcome) => {
                const value = point[outcome];
                const barHeight = (value / max) * height;
                const y = height - offset - barHeight;
                offset += barHeight;
                return value > 0 ? (
                  <rect
                    key={outcome}
                    x={index * barWidth + 2}
                    y={y}
                    width={Math.max(1, barWidth - 4)}
                    height={barHeight}
                    fill={OUTCOME_COLOURS[outcome]}
                  />
                ) : null;
              })}
            </g>
          );
        })}
        <line x1="0" x2={width} y1={height} y2={height} stroke="#94a3b8" />
      </svg>
      <ul className="mt-2 flex flex-wrap gap-4 text-xs text-slate-700">
        {OUTCOME_ORDER.map((outcome) => (
          <li key={outcome} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3 rounded-sm"
              style={{ backgroundColor: OUTCOME_COLOURS[outcome] }}
            />
            {OUTCOME_NAMES[outcome]}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DailyTable({ points }: { points: DayPoint[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="min-w-full text-left text-sm">
        <caption className="sr-only">Conversations per day</caption>
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="px-3 py-2">Day</th>
            <th scope="col" className="px-3 py-2">Started</th>
            <th scope="col" className="px-3 py-2">Engaged</th>
            {OUTCOME_ORDER.map((outcome) => (
              <th key={outcome} scope="col" className="px-3 py-2">{OUTCOME_NAMES[outcome]}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {points.map((point) => (
            <tr key={point.day}>
              <td className="px-3 py-2 text-slate-900">{point.day}</td>
              <td className="px-3 py-2 tabular-nums">{point.started}</td>
              <td className="px-3 py-2 tabular-nums">{point.engaged}</td>
              {OUTCOME_ORDER.map((outcome) => (
                <td key={outcome} className="px-3 py-2 tabular-nums">{point[outcome]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AnalyticsDashboard() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";

  const [preset, setPreset] = useState<RangePreset | "custom">("30d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [clients, setClients] = useState<ManagedClient[]>([]);
  // A Super Admin may leave this empty to see every client.
  const [clientId, setClientId] = useState("");

  const [range, setRange] = useState<RangeQuery>({ preset: "30d" });
  const [report, setReport] = useState<AnalyticsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rangeError, setRangeError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch(() => setClients([]));
  }, [isSuperAdmin]);

  const load = useCallback(async (query: RangeQuery) => {
    setLoading(true);
    setError(null);
    try {
      setReport(await getAnalytics(query));
    } catch (err) {
      setError(errorMessage(err, "The report could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load({ ...range, clientId: clientId || undefined });
    // The range and client are the only inputs; load is stable.
  }, [range, clientId, load]);

  function applyCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = customRangeProblem(customStart, customEnd);
    setRangeError(problem);
    if (problem) {
      return;
    }
    setRange({ start: customStart, end: customEnd });
  }

  function choosePreset(value: RangePreset | "custom") {
    setPreset(value);
    setRangeError(null);
    if (value !== "custom") {
      setRange({ preset: value });
    }
  }

  async function handleExport(kind: "summary" | "conversations") {
    setExporting(true);
    setExportMessage(null);
    try {
      await downloadExport(kind, { ...range, clientId: clientId || undefined });
      setExportMessage(`Downloaded the ${kind} CSV. It has no patient names, phone numbers or message text.`);
    } catch (err) {
      setExportMessage(errorMessage(err, "The export could not be downloaded."));
    } finally {
      setExporting(false);
    }
  }

  const summary = report?.summary;

  return (
    <div className="space-y-8">
      <section aria-labelledby="range-heading" className="space-y-4">
        <h2 id="range-heading" className="text-base font-semibold text-slate-900">
          Period
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          {isSuperAdmin ? (
            <div>
              <label htmlFor="analytics-client" className="block text-xs font-medium text-slate-600">
                Client
              </label>
              <select
                id="analytics-client"
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                className={`mt-1 ${selectClass}`}
              >
                <option value="">All clients</option>
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>{client.name}</option>
                ))}
              </select>
            </div>
          ) : null}
          <div>
            <label htmlFor="analytics-preset" className="block text-xs font-medium text-slate-600">
              Range
            </label>
            <select
              id="analytics-preset"
              value={preset}
              onChange={(event) => choosePreset(event.target.value as RangePreset | "custom")}
              className={`mt-1 ${selectClass}`}
            >
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="90d">Last 90 days</option>
              <option value="custom">Custom dates</option>
            </select>
          </div>
        </div>

        {preset === "custom" ? (
          <form onSubmit={applyCustom} className="flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor="range-start" className="block text-xs font-medium text-slate-600">From</label>
              <input
                id="range-start"
                type="date"
                value={customStart}
                onChange={(event) => setCustomStart(event.target.value)}
                className={`mt-1 ${selectClass}`}
              />
            </div>
            <div>
              <label htmlFor="range-end" className="block text-xs font-medium text-slate-600">To</label>
              <input
                id="range-end"
                type="date"
                value={customEnd}
                onChange={(event) => setCustomEnd(event.target.value)}
                className={`mt-1 ${selectClass}`}
              />
            </div>
            <button type="submit" className={buttonClass}>Apply</button>
            <p className="text-xs text-slate-500">Up to {MAX_RANGE_DAYS} days.</p>
          </form>
        ) : null}
        {rangeError ? (
          <p role="alert" className="text-sm text-rose-900">{rangeError}</p>
        ) : null}
        {report ? (
          <p className="text-xs text-slate-600">
            {report.period.start} to {report.period.end} ({report.period.days} days, {report.period.timezone}).
            {" "}{report.note}
          </p>
        ) : null}
      </section>

      {error ? (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">{error}</p>
      ) : null}
      {loading && !report ? <p className="text-sm text-slate-500">Loading the report...</p> : null}

      {report && summary ? (
        <>
          <section aria-labelledby="kpi-heading" className="space-y-3">
            <h2 id="kpi-heading" className="text-base font-semibold text-slate-900">Key numbers</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <KpiCard title="Conversations started" value={String(summary.started)}
                hint={`${summary.in_progress} still in progress`} />
              <KpiCard title="Engaged" value={String(summary.engaged)}
                hint={`${summary.bounced} left before a message`} />
              <KpiCard title="Booking rate" value={percent(summary.booking_rate)} hint="Of engaged conversations" />
              <KpiCard title="Callback rate" value={percent(summary.callback_rate)} hint="Of engaged conversations" />
              <KpiCard title="Handover rate" value={percent(summary.handover_rate)}
                hint={`Accepted by staff: ${percent(summary.handover_accept_rate)}`} />
              <KpiCard title="Resolution rate" value={percent(summary.resolution_rate)}
                hint="Answered, then the visitor left" />
              <KpiCard title="Abandonment rate" value={percent(summary.abandonment_rate)}
                hint="Ended after an unanswered question" />
              <KpiCard title="Average length" value={durationText(summary.average_duration_seconds)}
                hint="Engaged, ended conversations" />
            </div>
          </section>

          <section aria-labelledby="funnel-heading" className="space-y-3">
            <h2 id="funnel-heading" className="text-base font-semibold text-slate-900">Conversion funnel</h2>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Conversion funnel for the period</caption>
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-2">Step</th>
                    <th scope="col" className="px-4 py-2">Count</th>
                    <th scope="col" className="px-4 py-2">Share of engaged</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {report.funnel.map((step) => (
                    <tr key={step.key}>
                      <td className="px-4 py-2 text-slate-900">{step.label}</td>
                      <td className="px-4 py-2 tabular-nums">{step.count}</td>
                      <td className="px-4 py-2 tabular-nums">
                        {step.percent_of_engaged === null ? "-" : percent(step.percent_of_engaged)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="daily-heading" className="space-y-3">
            <h2 id="daily-heading" className="text-base font-semibold text-slate-900">Daily trend</h2>
            <DailyChart points={report.daily} />
            <DailyTable points={report.daily} />
          </section>

          <section aria-labelledby="outcomes-heading" className="space-y-3">
            <h2 id="outcomes-heading" className="text-base font-semibold text-slate-900">Outcome breakdown</h2>
            <p className="text-xs text-slate-600">
              Each engaged conversation has one outcome. Priority: Booking, then Callback, Handover,
              Resolved, Abandoned.
            </p>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Outcomes of engaged conversations</caption>
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-2">Outcome</th>
                    <th scope="col" className="px-4 py-2">Conversations</th>
                    <th scope="col" className="px-4 py-2">Share</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {report.outcomes.map((row) => (
                    <tr key={row.outcome}>
                      <td className="px-4 py-2 text-slate-900">{row.label}</td>
                      <td className="px-4 py-2 tabular-nums">{row.count}</td>
                      <td className="px-4 py-2 tabular-nums">{percent(row.percent_of_engaged)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="gaps-heading" className="space-y-3">
            <h2 id="gaps-heading" className="text-base font-semibold text-slate-900">
              Top unanswered questions
            </h2>
            {report.top_gaps.length === 0 ? (
              <p className="text-sm text-slate-600">No unanswered questions in this period.</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="min-w-full text-left text-sm">
                  <caption className="sr-only">Questions the knowledge base could not answer</caption>
                  <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-4 py-2">Question</th>
                      <th scope="col" className="px-4 py-2">Asked</th>
                      <th scope="col" className="px-4 py-2">Last asked</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {report.top_gaps.map((gap) => (
                      <tr key={gap.gap_id}>
                        <td className="max-w-md px-4 py-2 text-slate-900">{gap.question}</td>
                        <td className="px-4 py-2 tabular-nums">{gap.asks}</td>
                        <td className="px-4 py-2 text-slate-600">
                          {new Date(gap.last_asked_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-xs text-slate-500">
              Questions can contain health details. They are shown to admins only and are not in the
              CSV export.
            </p>
          </section>

          <section aria-labelledby="export-heading" className="space-y-3">
            <h2 id="export-heading" className="text-base font-semibold text-slate-900">Export</h2>
            <div className="flex flex-wrap gap-3">
              <button type="button" className={buttonClass} disabled={exporting}
                onClick={() => void handleExport("summary")}>
                Download summary (CSV)
              </button>
              <button type="button" className={buttonClass} disabled={exporting}
                onClick={() => void handleExport("conversations")}>
                Download conversation list (CSV)
              </button>
            </div>
            {exportMessage ? <p role="status" className="text-sm text-slate-700">{exportMessage}</p> : null}
          </section>
        </>
      ) : null}
    </div>
  );
}
