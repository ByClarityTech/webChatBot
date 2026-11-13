"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { useAdminUser } from "@/components/admin/admin-session";
import { GeneratedPassword } from "@/components/admin/generated-password";
import {
  ApiError,
  createUser,
  listClients,
  listUsers,
  ManagedClient,
  ManagedUser,
  resendInvite,
  updateUser,
} from "@/lib/admin-api";
import { canManageUser, creatableUserTypes, USER_TYPE_LABELS } from "@/lib/admin-permissions";
import { generatePassword } from "@/lib/passwords";

type Revealed = { password: string; forLabel: string };

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

const inputClass = "mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm";

/**
 * Setup is not finished. The API does not report a pending invite yet, so this is the best
 * signal available. Inactive users are excluded: the API refuses to invite them.
 */
function needsSetup(user: ManagedUser): boolean {
  return user.is_active && user.must_change_password;
}

export function UsersTable() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";
  const creatable = creatableUserTypes(actor.user_type);
  const actorRef = { user_type: actor.user_type, client_id: actor.client?.id ?? null };

  const [users, setUsers] = useState<ManagedUser[]>([]);
  // Only Super Admins can list clients; Admins always work in their own client.
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientFilter, setClientFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);

  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<"admin" | "support">(creatable[0] ?? "support");
  const [newClientId, setNewClientId] = useState("");
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const loadUsers = useCallback(async () => {
    setUsers(await listUsers(clientFilter || undefined));
  }, [clientFilter]);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch((err) => setError(errorMessage(err, "Failed to load clients.")));
  }, [isSuperAdmin]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await loadUsers();
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "Failed to load users."));
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
  }, [loadUsers]);

  const activeClients = clients.filter((client) => client.status === "active");

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = newEmail.trim();
    const fullName = newName.trim();
    if (!email || !fullName) {
      setError("Enter an email and a name.");
      return;
    }
    if (isSuperAdmin && !newClientId) {
      setError("Choose a client.");
      return;
    }

    setCreating(true);
    setError(null);
    setSuccess(null);
    setRevealed(null);
    try {
      await createUser({
        email,
        full_name: fullName,
        user_type: newType,
        client_id: isSuperAdmin ? newClientId : undefined,
      });
      setNewEmail("");
      setNewName("");
      setSuccess("User created and invite email sent.");
      await loadUsers();
    } catch (err) {
      setError(errorMessage(err, "Create failed."));
    } finally {
      setCreating(false);
    }
  }

  async function runAction(user: ManagedUser, action: () => Promise<void>, fallback: string) {
    setActionId(user.id);
    setError(null);
    setSuccess(null);
    try {
      await action();
      await loadUsers();
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setActionId(null);
    }
  }

  function handleRename(user: ManagedUser) {
    const fullName = editName.trim();
    if (!fullName || fullName === user.full_name) {
      setEditingId(null);
      return;
    }
    void runAction(
      user,
      async () => {
        await updateUser(user.id, { full_name: fullName });
        setEditingId(null);
        setSuccess(`Renamed ${user.email}.`);
      },
      "Rename failed.",
    );
  }

  function handleResetPassword(user: ManagedUser) {
    const confirmed = window.confirm(
      `Reset the password for ${user.email}? They are signed out everywhere and must use the new password.`,
    );
    if (!confirmed) {
      return;
    }
    const password = generatePassword();
    setRevealed(null);
    void runAction(
      user,
      async () => {
        await updateUser(user.id, { password });
        setSuccess(`Reset the password for ${user.email}.`);
        setRevealed({ password, forLabel: user.email });
      },
      "Password reset failed.",
    );
  }

  function handleResendInvite(user: ManagedUser) {
    void runAction(
      user,
      async () => {
        await resendInvite(user.id);
        setSuccess(`Sent a new invite to ${user.email}. Any earlier link no longer works.`);
      },
      "Resend failed.",
    );
  }

  function handleToggleActive(user: ManagedUser) {
    const activate = !user.is_active;
    if (!activate && !window.confirm(`Deactivate ${user.email}? They are signed out everywhere.`)) {
      return;
    }
    void runAction(
      user,
      async () => {
        await updateUser(user.id, { is_active: activate });
        setSuccess(`${activate ? "Activated" : "Deactivated"} ${user.email}.`);
      },
      activate ? "Activate failed." : "Deactivate failed.",
    );
  }

  return (
    <div className="space-y-6">
      {creatable.length > 0 && (
        <form
          onSubmit={handleCreate}
          className="rounded-2xl border border-slate-200 bg-white p-6"
          noValidate
        >
          <h2 className="text-lg font-semibold text-slate-900">Add user</h2>
          <div className="mt-4 flex flex-wrap items-end gap-4">
            <div>
              <label htmlFor="new-user-email" className="block text-xs font-medium text-slate-600">
                Email
              </label>
              <input
                id="new-user-email"
                type="email"
                value={newEmail}
                onChange={(event) => setNewEmail(event.target.value)}
                autoComplete="off"
                className={`${inputClass} w-64`}
                disabled={creating}
              />
            </div>
            <div>
              <label htmlFor="new-user-name" className="block text-xs font-medium text-slate-600">
                Full name
              </label>
              <input
                id="new-user-name"
                type="text"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                autoComplete="off"
                className={`${inputClass} w-56`}
                disabled={creating}
              />
            </div>
            {creatable.length > 1 && (
              <div>
                <label htmlFor="new-user-role" className="block text-xs font-medium text-slate-600">
                  Role
                </label>
                <select
                  id="new-user-role"
                  value={newType}
                  onChange={(event) => setNewType(event.target.value as "admin" | "support")}
                  className={inputClass}
                  disabled={creating}
                >
                  {creatable.map((type) => (
                    <option key={type} value={type}>
                      {USER_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {isSuperAdmin && (
              <div>
                <label htmlFor="new-user-client" className="block text-xs font-medium text-slate-600">
                  Client
                </label>
                <select
                  id="new-user-client"
                  value={newClientId}
                  onChange={(event) => setNewClientId(event.target.value)}
                  className={inputClass}
                  disabled={creating}
                >
                  <option value="">Choose a client</option>
                  {activeClients.map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <button
              type="submit"
              disabled={creating}
              className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating ? "Creating..." : creatable.length > 1 ? "Create user" : "Create Support user"}
            </button>
          </div>
          {isSuperAdmin && activeClients.length === 0 && (
            <p className="mt-3 text-xs text-slate-500">
              There are no active clients yet.{" "}
              <Link href="/admin/clients" className="underline">
                Create a client first.
              </Link>
            </p>
          )}
        </form>
      )}

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      {success && <p className="text-sm text-green-700">{success}</p>}

      {revealed && (
        <GeneratedPassword
          password={revealed.password}
          forLabel={revealed.forLabel}
          onDone={() => setRevealed(null)}
        />
      )}

      <div className="flex flex-wrap items-end gap-4">
        {isSuperAdmin && (
          <div>
            <label htmlFor="client-filter" className="block text-xs font-medium text-slate-600">
              Client
            </label>
            <select
              id="client-filter"
              value={clientFilter}
              onChange={(event) => setClientFilter(event.target.value)}
              className={inputClass}
            >
              <option value="">All clients</option>
              {clients.map((client) => (
                <option key={client.id} value={client.id}>
                  {client.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <p className="text-sm text-slate-600">
          {users.length} user{users.length === 1 ? "" : "s"}
        </p>
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Name</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Email</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Role</th>
              {isSuperAdmin && (
                <th className="px-4 py-3 text-left font-medium text-slate-700">Client</th>
              )}
              <th className="px-4 py-3 text-left font-medium text-slate-700">Status</th>
              <th className="px-4 py-3 text-left font-medium text-slate-700">Last sign-in</th>
              <th className="px-4 py-3 text-right font-medium text-slate-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={isSuperAdmin ? 7 : 6} className="px-4 py-8 text-center text-slate-500">
                  Loading users...
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={isSuperAdmin ? 7 : 6} className="px-4 py-8 text-center text-slate-500">
                  No users found.
                </td>
              </tr>
            ) : (
              users.map((user) => {
                const busy = actionId === user.id;
                const manageable = canManageUser(actorRef, user);
                const editing = editingId === user.id;
                return (
                  <tr key={user.id}>
                    <td className="px-4 py-3 font-medium text-slate-900">
                      {editing ? (
                        <input
                          aria-label={`New name for ${user.email}`}
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              handleRename(user);
                            } else if (event.key === "Escape") {
                              setEditingId(null);
                            }
                          }}
                          className="w-44 rounded-lg border border-slate-300 px-2 py-1 text-sm"
                          disabled={busy}
                          autoFocus
                        />
                      ) : (
                        <>
                          {user.full_name}
                          {user.id === actor.id && (
                            <span className="ml-2 text-xs font-normal text-slate-500">(you)</span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{user.email}</td>
                    <td className="px-4 py-3 text-slate-600">{USER_TYPE_LABELS[user.user_type]}</td>
                    {isSuperAdmin && (
                      <td className="px-4 py-3 text-slate-600">{user.client_name ?? "All clients"}</td>
                    )}
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          user.is_active ? "bg-green-100 text-green-800" : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {user.is_active ? "Active" : "Inactive"}
                      </span>
                      {user.is_active && user.must_change_password && (
                        <span className="ml-2 inline-flex rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                          Password not set
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{formatDate(user.last_login_at)}</td>
                    <td className="px-4 py-3 text-right">
                      {!manageable ? (
                        <span className="text-slate-400">—</span>
                      ) : editing ? (
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleRename(user)}
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
                              setEditingId(user.id);
                              setEditName(user.full_name);
                            }}
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            Rename
                          </button>
                          {needsSetup(user) && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => handleResendInvite(user)}
                              className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              Resend invite
                            </button>
                          )}
                          {user.is_active && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => handleResetPassword(user)}
                              className="rounded-lg border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              Reset password
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleToggleActive(user)}
                            className={
                              user.is_active
                                ? "rounded-lg border border-red-200 px-3 py-1.5 text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                                : "rounded-lg border border-green-200 px-3 py-1.5 text-green-700 hover:bg-green-50 disabled:cursor-not-allowed disabled:opacity-50"
                            }
                          >
                            {busy ? "Saving..." : user.is_active ? "Deactivate" : "Activate"}
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
