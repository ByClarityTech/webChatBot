"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, createClient, listClients, ManagedClient, updateClient } from "@/lib/admin-api";

// Mirrors SLUG_PATTERN in backend/app/schemas/users.py; the API checks again.
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const SLUG_MIN_LENGTH = 2;
const SLUG_MAX_LENGTH = 100;

/** "Smile Dental, Chennai" -> "smile-dental-chennai" */
function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/, "");
}

function slugProblem(slug: string): string | null {
  if (slug.length < SLUG_MIN_LENGTH || slug.length > SLUG_MAX_LENGTH) {
    return `Slug must be ${SLUG_MIN_LENGTH} to ${SLUG_MAX_LENGTH} characters.`;
  }
  if (!SLUG_PATTERN.test(slug)) {
    return "Slug may use lowercase letters, digits and single hyphens between them.";
  }
  return null;
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

const inputClass = "mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm";

export function ClientsTable() {
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newSlug, setNewSlug] = useState("");
  // Until the slug is typed into, it follows the name.
  const [slugEdited, setSlugEdited] = useState(false);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const loadClients = useCallback(async () => {
    setClients(await listClients());
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await loadClients();
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "Failed to load clients."));
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
  }, [loadClients]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newName.trim();
    const slug = newSlug.trim();
    if (!name) {
      setError("Enter a client name.");
      return;
    }
    const problem = slugProblem(slug);
    if (problem) {
      setError(problem);
      return;
    }

    setCreating(true);
    setError(null);
    setSuccess(null);
    try {
      const client = await createClient(name, slug);
      setNewName("");
      setNewSlug("");
      setSlugEdited(false);
      setSuccess(`Created client ${client.name}. Add its first Admin on the Users page.`);
      await loadClients();
    } catch (err) {
      setError(errorMessage(err, "Create failed."));
    } finally {
      setCreating(false);
    }
  }

  async function runAction(client: ManagedClient, action: () => Promise<void>, fallback: string) {
    setActionId(client.id);
    setError(null);
    setSuccess(null);
    try {
      await action();
      await loadClients();
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setActionId(null);
    }
  }

  function handleRename(client: ManagedClient) {
    const name = editName.trim();
    if (!name || name === client.name) {
      setEditingId(null);
      return;
    }
    void runAction(
      client,
      async () => {
        await updateClient(client.id, { name });
        setEditingId(null);
        setSuccess(`Renamed client to ${name}.`);
      },
      "Rename failed.",
    );
  }

  function handleToggleStatus(client: ManagedClient) {
    const suspend = client.status === "active";
    if (
      suspend &&
      !window.confirm(
        `Suspend ${client.name}? Its ${client.user_count} user${client.user_count === 1 ? "" : "s"} are signed out and cannot sign in until it is activated.`,
      )
    ) {
      return;
    }
    void runAction(
      client,
      async () => {
        await updateClient(client.id, { status: suspend ? "suspended" : "active" });
        setSuccess(`${suspend ? "Suspended" : "Activated"} ${client.name}.`);
      },
      suspend ? "Suspend failed." : "Activate failed.",
    );
  }

  return (
    <div className="space-y-6">
      <form onSubmit={handleCreate} className="rounded-2xl border border-slate-200 bg-white p-6" noValidate>
        <h2 className="text-lg font-semibold text-slate-900">Add client</h2>
        <div className="mt-4 flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="new-client-name" className="block text-xs font-medium text-slate-600">
              Name
            </label>
            <input
              id="new-client-name"
              type="text"
              value={newName}
              onChange={(event) => {
                setNewName(event.target.value);
                if (!slugEdited) {
                  setNewSlug(slugFromName(event.target.value));
                }
              }}
              autoComplete="off"
              className={`${inputClass} w-64`}
              disabled={creating}
            />
          </div>
          <div>
            <label htmlFor="new-client-slug" className="block text-xs font-medium text-slate-600">
              Slug
            </label>
            <input
              id="new-client-slug"
              type="text"
              value={newSlug}
              onChange={(event) => {
                setNewSlug(event.target.value.toLowerCase());
                setSlugEdited(true);
              }}
              autoComplete="off"
              className={`${inputClass} w-56 font-mono`}
              disabled={creating}
            />
          </div>
          <button
            type="submit"
            disabled={creating}
            className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {creating ? "Creating..." : "Create client"}
          </button>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          The slug identifies the client and cannot be changed later.
        </p>
      </form>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      {success && <p className="text-sm text-green-700">{success}</p>}

      <p className="text-sm text-slate-600">
        {clients.length} client{clients.length === 1 ? "" : "s"}
      </p>

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Name</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Slug</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Status</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Users</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Created</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  Loading clients...
                </td>
              </tr>
            ) : clients.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  No clients yet. Add one above.
                </td>
              </tr>
            ) : (
              clients.map((client) => {
                const busy = actionId === client.id;
                const editing = editingId === client.id;
                const active = client.status === "active";
                return (
                  <tr key={client.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">
                      {editing ? (
                        <input
                          aria-label={`New name for ${client.slug}`}
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              handleRename(client);
                            } else if (event.key === "Escape") {
                              setEditingId(null);
                            }
                          }}
                          className="w-52 rounded-lg border border-slate-300 px-2 py-1 text-sm"
                          disabled={busy}
                          autoFocus
                        />
                      ) : (
                        client.name
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-600">{client.slug}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          active ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {active ? "Active" : "Suspended"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{client.user_count}</td>
                    <td className="px-4 py-3 text-slate-600">{formatDate(client.created_at)}</td>
                    <td className="px-4 py-3 text-right">
                      {editing ? (
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRename(client)}
                            className="rounded-lg bg-slate-900 px-3 py-1.5 text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {busy ? "Saving..." : "Save"}
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setEditingId(null)}
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setEditingId(client.id);
                              setEditName(client.name);
                            }}
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleToggleStatus(client)}
                            className={
                              active
                                ? "rounded-lg border border-red-200 px-3 py-1.5 text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                                : "rounded-lg border border-green-200 px-3 py-1.5 text-green-700 hover:bg-green-50 disabled:cursor-not-allowed disabled:opacity-50"
                            }
                          >
                            {busy ? "Saving..." : active ? "Suspend" : "Activate"}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
