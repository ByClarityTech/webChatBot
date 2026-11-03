"use client";

import { useCallback, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ApiError, AuditEntry, listAuditLog, listClients, ManagedClient } from "@/lib/admin-api";

// Action names written by the backend (log_admin_action calls) and scripts/create_super_admin.py.
// Unknown ones show as-is.
const ACTION_LABELS: Record<string, string> = {
  "super_admin.create": "Created Super Admin (script)",
  "super_admin.password_reset": "Reset Super Admin password (script)",
  "user.create": "Created user",
  "user.update": "Renamed user",
  "user.password_reset": "Reset user password",
  "user.password_change": "Changed own password",
  "user.activate": "Activated user",
  "user.deactivate": "Deactivated user",
  "client.create": "Created client",
  "client.update": "Renamed client",
  "client.suspend": "Suspended client",
  "client.activate": "Activated client",
  "document.upload": "Uploaded document",
  "document.delete": "Deleted document",
  "document.reindex": "Reindexed document",
  "booking.cancel": "Cancelled booking",
  "calendar.disconnect": "Disconnected calendar",
  "settings.business_hours_update": "Updated business hours",
};

// The API returns at most 200 entries and has no paging yet.
const LIMIT_OPTIONS = [50, 100, 200] as const;

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function AuditLogTable() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";

  const [entries, setEntries] = useState<AuditEntry[]>([]);
  // Super Admin only: client names for the filter and the Client column.
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientFilter, setClientFilter] = useState("");
  const [limit, setLimit] = useState<number>(LIMIT_OPTIONS[0]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadEntries = useCallback(async () => {
    setEntries(await listAuditLog({ limit, clientId: clientFilter || undefined }));
  }, [limit, clientFilter]);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch((err) => setError(errorMessage(err, "Failed to load clients.")));
  }, [isSuperAdmin]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await loadEntries();
    } catch (err) {
      setError(errorMessage(err, "Failed to load the audit log."));
    } finally {
      setLoading(false);
    }
  }, [loadEntries]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const clientNames = new Map(clients.map((client) => [client.id, client.name]));

  function clientLabel(clientId: string | null): string {
    if (!clientId) {
      return "Platform";
    }
    return clientNames.get(clientId) ?? clientId.slice(0, 8);
  }

  const columns = isSuperAdmin ? 6 : 5;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        {isSuperAdmin && (
          <div>
            <label htmlFor="audit-client-filter" className="block text-xs font-medium text-slate-600">
              Client
            </label>
            <select
              id="audit-client-filter"
              value={clientFilter}
              onChange={(event) => setClientFilter(event.target.value)}
              className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">All, including platform</option>
              {clients.map((client) => (
                <option key={client.id} value={client.id}>
                  {client.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label htmlFor="audit-limit" className="block text-xs font-medium text-slate-600">
            Show
          </label>
          <select
            id="audit-limit"
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value))}
            className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            {LIMIT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                Latest {option}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => void reload()}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Refresh
        </button>
        <p className="text-sm text-slate-600">
          {entries.length} entr{entries.length === 1 ? "y" : "ies"}
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Time</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Action</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">By</th>
              {isSuperAdmin && (
                <th className="px-4 py-3 text-left font-medium text-slate-700">Client</th>
              )}
              <th className="px-4 py-3 text-left font-medium text-slate-700">Item</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={columns} className="px-4 py-8 text-center text-slate-500">
                  Loading audit log...
                </td>
              </tr>
            ) : entries.length === 0 ? (
              <tr>
                <td colSpan={columns} className="px-4 py-8 text-center text-slate-500">
                  No entries yet.
                </td>
              </tr>
            ) : (
              entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                    {formatTime(entry.created_at)}
                  </td>
                  <td className="px-4 py-3 font-medium text-slate-900" title={entry.action}>
                    {ACTION_LABELS[entry.action] ?? entry.action}
                  </td>
                  {/* No user: a deleted user (user_id is set to NULL) or an entry from before sign-in. */}
                  <td className="px-4 py-3 text-slate-600">{entry.user_email ?? "—"}</td>
                  {isSuperAdmin && (
                    <td className="px-4 py-3 text-slate-600">{clientLabel(entry.client_id)}</td>
                  )}
                  <td className="px-4 py-3 text-slate-600">
                    {entry.resource_type ? (
                      <span title={entry.resource_id ?? undefined}>
                        {entry.resource_type}
                        {entry.resource_id && (
                          <span className="ml-1 font-mono text-xs text-slate-400">
                            {entry.resource_id.slice(0, 8)}
                          </span>
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">
                    {entry.client_ip ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
