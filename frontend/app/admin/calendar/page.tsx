"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { adminFetch } from "@/lib/admin-api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type OAuthStatus = {
  connected: boolean;
  /** Connected before, but Google refused it (revoked or expired): connect again. */
  needs_reconnect?: boolean;
  calendar_id: string | null;
  expires_at: string | null;
  connected_email: string | null;
  refresh_error: string | null;
};

type CalendarMode = "google" | "built_in" | "none";

type CalendarSettings = {
  calendar_mode: CalendarMode;
  google_connected: boolean;
  google_needs_reconnect: boolean;
  booking_available: boolean;
};

const MODE_OPTIONS: { value: CalendarMode; label: string; description: string }[] = [
  {
    value: "google",
    label: "Google Calendar",
    description: "Open times come from your Google Calendar; visitors get a Google invite.",
  },
  {
    value: "built_in",
    label: "Built-in calendar",
    description: "Open times are your business hours minus bookings made here. No Google account.",
  },
  {
    value: "none",
    label: "No online booking",
    description: "Visitors are not offered booking in the chat.",
  },
];

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

function isExpiringWithinDays(expiresAt: string | null, days: number): boolean {
  if (!expiresAt) {
    return false;
  }
  const expiry = new Date(expiresAt);
  if (Number.isNaN(expiry.getTime())) {
    return false;
  }
  const daysUntilExpiry = (expiry.getTime() - Date.now()) / (1000 * 60 * 60 * 24);
  return daysUntilExpiry >= 0 && daysUntilExpiry < days;
}

function clientIdFromUrl(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return new URLSearchParams(window.location.search).get("client_id") ?? "";
}

export default function AdminCalendarPage() {
  const router = useRouter();
  // After Google, a Super Admin comes back with ?client_id= for the client just connected.
  const clientScope = useClientScope(clientIdFromUrl());
  const { needsClient, withClient } = clientScope;
  const [status, setStatus] = useState<OAuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  // The saved mode, and the option shown (Google can be shown before it is connected).
  const [settings, setSettings] = useState<CalendarSettings | null>(null);
  const [chosen, setChosen] = useState<CalendarMode>("none");
  const [savingMode, setSavingMode] = useState(false);

  const loadSettings = useCallback(async () => {
    const response = await adminFetch(withClient("/api/v1/admin/calendar"));
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const data = (await response.json()) as CalendarSettings;
    setSettings(data);
    setChosen(data.calendar_mode);
    return data;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- withClient changes with scope
  }, [clientScope.scope]);

  const loadStatus = useCallback(async () => {
    setError(null);
    const response = await adminFetch(withClient("/api/v1/admin/google/oauth/status"));
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    return (await response.json()) as OAuthStatus;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- withClient changes with scope
  }, [clientScope.scope]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (needsClient) {
        setStatus(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const [data] = await Promise.all([loadStatus(), loadSettings()]);
        if (!cancelled) {
          setStatus(data);
          if (data.refresh_error) {
            setError(data.refresh_error);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load calendar status.");
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
  }, [loadStatus, loadSettings, needsClient]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const oauthError = params.get("error");

    if (!connected && !oauthError) {
      return;
    }

    const returnedClient = params.get("client_id");
    router.replace(
      returnedClient
        ? `/admin/calendar?client_id=${encodeURIComponent(returnedClient)}`
        : "/admin/calendar",
    );

    if (connected === "1") {
      setSuccess("Google Calendar connected successfully.");
      setError(null);
      void loadStatus()
        .then((data) => {
          setStatus(data);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Failed to load calendar status.");
        });
      return;
    }

    if (oauthError) {
      setError(oauthError);
      setSuccess(null);
    }
  }, [router, loadStatus]);

  async function handleConnect() {
    setConnecting(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await adminFetch(withClient("/api/v1/admin/google/oauth/start"));
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const data = (await response.json()) as { auth_url: string };
      window.location.href = data.auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start OAuth flow.");
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!window.confirm("Disconnect Google Calendar? Booking availability will stop working.")) {
      return;
    }

    setDisconnecting(true);
    setError(null);
    setSuccess(null);

    try {
      const response = await adminFetch(withClient("/api/v1/admin/google/oauth/disconnect"), {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      setStatus({
        connected: false,
        calendar_id: null,
        expires_at: null,
        connected_email: null,
        refresh_error: null,
      });
      const after = await loadSettings();
      setSuccess(
        after.calendar_mode === "none"
          ? "Google Calendar disconnected. Online booking is off until you choose a calendar."
          : "Google Calendar disconnected.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect calendar.");
    } finally {
      setDisconnecting(false);
    }
  }

  async function handleChooseMode(mode: CalendarMode) {
    setError(null);
    setSuccess(null);
    // Google is saved when the connection exists; otherwise show the Connect button first.
    if (mode === "google" && !settings?.google_connected) {
      setChosen("google");
      return;
    }
    setSavingMode(true);
    try {
      const response = await adminFetch(withClient("/api/v1/admin/calendar/mode"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const data = (await response.json()) as CalendarSettings;
      setSettings(data);
      setChosen(data.calendar_mode);
      setSuccess(
        mode === "none"
          ? "Online booking is off for this client."
          : `Saved. Visitors book from the ${mode === "google" ? "Google" : "built-in"} calendar.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the calendar.");
    } finally {
      setSavingMode(false);
    }
  }

  const expiryInfo =
    status?.connected && !status.refresh_error && isExpiringWithinDays(status.expires_at, 7);

  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Calendar</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Choose where visitors&apos; open booking times come from. Each client has its own
        calendar.
      </p>

      <div className="mt-6">
        <ClientPicker scope={clientScope} id="calendar-client" />
      </div>

      {error ? (
        <div className="mt-6 max-w-xl rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}
      {success ? (
        <div className="mt-6 max-w-xl rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {success}
        </div>
      ) : null}

      {!needsClient && !loading && settings ? (
        <fieldset className="mt-8 max-w-xl space-y-2" disabled={savingMode}>
          <legend className="mb-2 text-sm font-medium text-slate-900">Booking calendar</legend>
          {MODE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer gap-3 rounded-xl border p-4 text-sm ${
                chosen === option.value ? "border-slate-900 bg-slate-50" : "border-slate-200 bg-white"
              }`}
            >
              <input
                type="radio"
                name="calendar-mode"
                value={option.value}
                checked={chosen === option.value}
                onChange={() => void handleChooseMode(option.value)}
                className="mt-1"
              />
              <span>
                <span className="block font-medium text-slate-900">
                  {option.label}
                  {settings.calendar_mode === option.value ? (
                    <span className="ml-2 text-xs font-normal text-slate-500">(in use)</span>
                  ) : null}
                </span>
                <span className="block text-slate-600">{option.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}

      {!needsClient && !loading && chosen === "built_in" ? (
        <div className="mt-6 max-w-xl space-y-2 rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          <p>
            Open times are this client&apos;s business hours, minus bookings already made here,
            with the buffer and minimum notice from{" "}
            <Link href="/admin/settings" className="font-medium text-slate-900 underline">
              Settings
            </Link>
            .
          </p>
          <p>
            Other meetings in a Google Calendar do not block times here, and visitors get no
            calendar invite: they see the booked time in the chat.
          </p>
        </div>
      ) : null}

      {!needsClient && !loading && chosen === "none" ? (
        <div className="mt-6 max-w-xl rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Online booking is off. The assistant answers questions but does not offer to book.
        </div>
      ) : null}

      <div
        className={`mt-6 max-w-xl rounded-xl border border-slate-200 bg-white p-6 ${
          !needsClient && !loading && chosen !== "google" ? "hidden" : ""
        }`}
      >
        {needsClient ? (
          <p className="text-sm text-slate-500">Choose a client to see and connect its calendar.</p>
        ) : loading ? (
          <p className="text-sm text-slate-500">Loading connection status...</p>
        ) : status?.connected ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
              <p className="text-sm font-medium text-slate-900">Connected</p>
            </div>

            <dl className="grid gap-3 text-sm">
              <div>
                <dt className="text-slate-500">Account</dt>
                <dd className="font-medium text-slate-900">
                  {status.connected_email ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Calendar ID</dt>
                <dd className="font-medium text-slate-900">{status.calendar_id ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Access token expires</dt>
                <dd className="font-medium text-slate-900">
                  {status.expires_at
                    ? new Date(status.expires_at).toLocaleString()
                    : "—"}
                </dd>
              </div>
            </dl>

            {expiryInfo ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                Access token expires within 7 days. It refreshes automatically while connected.
              </div>
            ) : null}

            <button
              type="button"
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              {disconnecting ? "Disconnecting..." : "Disconnect calendar"}
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {status?.needs_reconnect ? (
              <div className="flex items-center gap-2">
                <span className="inline-block h-2.5 w-2.5 rounded-full bg-amber-500" />
                <p className="text-sm font-medium text-slate-900">
                  Reconnect needed{status.connected_email ? ` (${status.connected_email})` : ""}
                </p>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="inline-block h-2.5 w-2.5 rounded-full bg-slate-300" />
                <p className="text-sm font-medium text-slate-900">Not connected</p>
              </div>
            )}
            {status?.needs_reconnect ? (
              <p className="text-sm text-amber-800">
                Google stopped accepting this connection, so online booking is off for this
                client. Connect again to turn it back on.
              </p>
            ) : null}
            <p className="text-sm text-slate-600">
              You will be redirected to Google to authorize calendar access. After consent, you
              return to this page automatically.
            </p>
            <p className="text-xs text-slate-500">
              OAuth callback:{" "}
              <code className="rounded bg-slate-100 px-1">
                {API_URL}/api/v1/admin/google/oauth/callback
              </code>
            </p>
            <button
              type="button"
              onClick={handleConnect}
              disabled={connecting}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {connecting
                ? "Redirecting..."
                : status?.needs_reconnect
                  ? "Reconnect Google Calendar"
                  : "Connect Google Calendar"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
