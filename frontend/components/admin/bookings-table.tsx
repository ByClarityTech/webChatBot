"use client";

import { useCallback, useEffect, useState } from "react";

import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { adminFetch } from "@/lib/admin-api";

export type AdminBooking = {
  id: string;
  name: string;
  email: string;
  slot_start: string;
  slot_end: string;
  status: string;
};

type BookingsResponse = {
  bookings: AdminBooking[];
  total: number;
  offset: number;
  limit: number;
};

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "confirmed", label: "Confirmed" },
  { value: "cancelled", label: "Cancelled" },
] as const;

const PAGE_SIZE = 25;

function formatSlot(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function statusBadgeClass(status: string): string {
  if (status === "cancelled") {
    return "bg-slate-100 text-slate-600";
  }
  if (status === "confirmed") {
    return "bg-green-100 text-green-800";
  }
  return "bg-amber-100 text-amber-800";
}

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

export function BookingsTable() {
  const clientScope = useClientScope();
  const { needsClient, withClient } = clientScope;
  const [bookings, setBookings] = useState<AdminBooking[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  const loadBookings = useCallback(async () => {
    setError(null);
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(PAGE_SIZE),
    });
    if (statusFilter) {
      params.set("status", statusFilter);
    }
    if (fromDate) {
      params.set("from", `${fromDate}T00:00:00`);
    }
    if (toDate) {
      params.set("to", `${toDate}T23:59:59`);
    }

    if (needsClient) {
      setBookings([]);
      setTotal(0);
      return;
    }
    if (clientScope.scope) {
      params.set("client_id", clientScope.scope);
    }
    const response = await adminFetch(`/api/v1/admin/bookings?${params.toString()}`);
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const data = (await response.json()) as BookingsResponse;
    setBookings(data.bookings);
    setTotal(data.total);
  }, [offset, statusFilter, fromDate, toDate, needsClient, clientScope.scope]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        await loadBookings();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load bookings.");
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
  }, [loadBookings]);

  function handleFilterChange() {
    setOffset(0);
  }

  async function handleCancel(booking: AdminBooking) {
    const confirmed = window.confirm(
      `Cancel booking for ${booking.name} on ${formatSlot(booking.slot_start)}?`,
    );
    if (!confirmed) {
      return;
    }

    setActionId(booking.id);
    setError(null);
    setSuccess(null);

    try {
      const response = await adminFetch(withClient(`/api/v1/admin/bookings/${booking.id}`), {
        method: "PATCH",
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      setSuccess(`Cancelled booking for ${booking.name}.`);
      await loadBookings();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed.");
    } finally {
      setActionId(null);
    }
  }

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <ClientPicker scope={clientScope} id="bookings-client" />
        <div>
          <label className="block text-xs font-medium text-slate-600">From date</label>
          <input
            type="date"
            value={fromDate}
            onChange={(event) => {
              setFromDate(event.target.value);
              handleFilterChange();
            }}
            className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">To date</label>
          <input
            type="date"
            value={toDate}
            onChange={(event) => {
              setToDate(event.target.value);
              handleFilterChange();
            }}
            className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-600">Status</label>
          <select
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value);
              handleFilterChange();
            }}
            className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <p className="text-sm text-slate-600">
          {total} booking{total === 1 ? "" : "s"}
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {success && <p className="text-sm text-green-700">{success}</p>}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Name</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Email</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Slot start</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Slot end</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Status</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {needsClient ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Choose a client to see its bookings.
                </td>
              </tr>
            ) : loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Loading bookings...
                </td>
              </tr>
            ) : bookings.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  No bookings found. Create one via the chat widget or booking API.
                </td>
              </tr>
            ) : (
              bookings.map((booking) => {
                const busy = actionId === booking.id;
                const canCancel = booking.status !== "cancelled";
                return (
                  <tr key={booking.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">{booking.name}</td>
                    <td className="px-4 py-3 text-slate-600">{booking.email}</td>
                    <td className="px-4 py-3 text-slate-600">{formatSlot(booking.slot_start)}</td>
                    <td className="px-4 py-3 text-slate-600">{formatSlot(booking.slot_end)}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${statusBadgeClass(booking.status)}`}
                      >
                        {booking.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {canCancel ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void handleCancel(booking)}
                          className="rounded-lg border border-red-200 px-3 py-1.5 text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? "Cancelling..." : "Cancel"}
                        </button>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <p>
            Showing {pageStart}–{pageEnd} of {total}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={!canPrev || loading}
              onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
              className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!canNext || loading}
              onClick={() => setOffset((value) => value + PAGE_SIZE)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
