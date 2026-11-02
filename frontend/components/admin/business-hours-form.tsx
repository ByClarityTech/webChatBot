"use client";

import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "@/lib/admin-api";

export type BusinessHoursSettings = {
  business_days: number[];
  start_time: string;
  end_time: string;
  slot_duration_minutes: number;
  slot_buffer_minutes: number;
  timezone: string;
  source: string;
  updated_at: string | null;
};

const WEEKDAYS = [
  { value: 1, label: "Mon" },
  { value: 2, label: "Tue" },
  { value: 3, label: "Wed" },
  { value: 4, label: "Thu" },
  { value: 5, label: "Fri" },
  { value: 6, label: "Sat" },
  { value: 7, label: "Sun" },
] as const;

const TIMEZONE_OPTIONS = [
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Australia/Sydney",
  "UTC",
];

async function parseError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string | Array<{ msg?: string }> };
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) => item.msg)
        .filter((msg): msg is string => Boolean(msg));
      if (messages.length > 0) {
        return messages.join(", ");
      }
    }
  } catch {
    // not JSON
  }
  return text || `Request failed (${response.status})`;
}

function emptyForm(): Omit<BusinessHoursSettings, "source" | "updated_at"> {
  return {
    business_days: [1, 2, 3, 4, 5],
    start_time: "09:00",
    end_time: "17:00",
    slot_duration_minutes: 30,
    slot_buffer_minutes: 15,
    timezone: "Asia/Kolkata",
  };
}

export function BusinessHoursForm() {
  const [form, setForm] = useState(emptyForm());
  const [source, setSource] = useState<string>("env");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setError(null);
    const response = await adminFetch("/api/v1/admin/settings/business-hours");
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const data = (await response.json()) as BusinessHoursSettings;
    setForm({
      business_days: data.business_days,
      start_time: data.start_time,
      end_time: data.end_time,
      slot_duration_minutes: data.slot_duration_minutes,
      slot_buffer_minutes: data.slot_buffer_minutes,
      timezone: data.timezone,
    });
    setSource(data.source);
    setUpdatedAt(data.updated_at);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        await loadSettings();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load settings.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [loadSettings]);

  function toggleDay(day: number) {
    setForm((prev) => {
      const selected = prev.business_days.includes(day)
        ? prev.business_days.filter((d) => d !== day)
        : [...prev.business_days, day].sort((a, b) => a - b);
      return { ...prev, business_days: selected };
    });
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);

    if (form.business_days.length === 0) {
      setError("Select at least one business day.");
      setSaving(false);
      return;
    }

    try {
      const response = await adminFetch("/api/v1/admin/settings/business-hours", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const data = (await response.json()) as BusinessHoursSettings;
      setSource(data.source);
      setUpdatedAt(data.updated_at);
      setSuccess("Business hours saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">Loading settings...</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-6">
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}
      {success ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {success}
        </div>
      ) : null}

      <div>
        <p className="text-sm font-medium text-slate-700">Business days</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {WEEKDAYS.map((day) => {
            const selected = form.business_days.includes(day.value);
            return (
              <button
                key={day.value}
                type="button"
                onClick={() => toggleDay(day.value)}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                  selected
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {day.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-700">Start time</span>
          <input
            type="time"
            required
            value={form.start_time}
            onChange={(e) => setForm((prev) => ({ ...prev, start_time: e.target.value }))}
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-slate-900"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-slate-700">End time</span>
          <input
            type="time"
            required
            value={form.end_time}
            onChange={(e) => setForm((prev) => ({ ...prev, end_time: e.target.value }))}
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-slate-900"
          />
        </label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-700">Slot duration (minutes)</span>
          <input
            type="number"
            required
            min={5}
            max={480}
            value={form.slot_duration_minutes}
            onChange={(e) =>
              setForm((prev) => ({
                ...prev,
                slot_duration_minutes: Number(e.target.value),
              }))
            }
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-slate-900"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-slate-700">Buffer between meetings (minutes)</span>
          <input
            type="number"
            required
            min={0}
            max={120}
            value={form.slot_buffer_minutes}
            onChange={(e) =>
              setForm((prev) => ({
                ...prev,
                slot_buffer_minutes: Number(e.target.value),
              }))
            }
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-slate-900"
          />
        </label>
      </div>

      <label className="block text-sm">
        <span className="font-medium text-slate-700">Timezone</span>
        <select
          required
          value={form.timezone}
          onChange={(e) => setForm((prev) => ({ ...prev, timezone: e.target.value }))}
          className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-slate-900"
        >
          {TIMEZONE_OPTIONS.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-center gap-4">
        <button
          type="submit"
          disabled={saving}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save settings"}
        </button>
        <p className="text-xs text-slate-500">
          Source: {source}
          {updatedAt ? ` · Updated ${new Date(updatedAt).toLocaleString()}` : null}
        </p>
      </div>
    </form>
  );
}
