"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useAdminUser, useRefreshAdminUser } from "@/components/admin/admin-session";
import { ApiError, changeOwnPassword } from "@/lib/admin-api";
import { USER_TYPE_LABELS } from "@/lib/admin-permissions";
import { passwordProblem, PASSWORD_MIN_LENGTH } from "@/lib/passwords";

function changeErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      const minutes = err.retryAfterSeconds ? Math.ceil(err.retryAfterSeconds / 60) : null;
      return minutes
        ? `Too many wrong attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`
        : "Too many wrong attempts. Try again later.";
    }
    return err.message;
  }
  return "Could not reach the server. Check that the API is running.";
}

export default function AccountPage() {
  const user = useAdminUser();
  const refresh = useRefreshAdminUser();
  const router = useRouter();
  const required = user.must_change_password;

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function validate(): string | null {
    if (!currentPassword) {
      return "Enter your current password.";
    }
    const problem = passwordProblem(newPassword, user.email);
    if (problem) {
      return problem;
    }
    if (newPassword === currentPassword) {
      return "The new password must be different from the current one.";
    }
    if (newPassword !== confirmPassword) {
      return "The new passwords do not match.";
    }
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await changeOwnPassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(true);
      const wasRequired = required;
      await refresh();
      if (wasRequired) {
        router.replace("/admin");
      }
    } catch (err) {
      setError(changeErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const inputClass =
    "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2";

  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">My account</h1>

      {required && (
        <p className="mt-4 max-w-2xl rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          An administrator set your password. Choose your own password to continue.
        </p>
      )}

      <dl className="mt-6 grid max-w-2xl grid-cols-[10rem_1fr] gap-y-2 rounded-2xl border border-slate-200 bg-white p-6 text-sm">
        <dt className="text-slate-500">Name</dt>
        <dd className="text-slate-900">{user.full_name}</dd>
        <dt className="text-slate-500">Email</dt>
        <dd className="text-slate-900">{user.email}</dd>
        <dt className="text-slate-500">Role</dt>
        <dd className="text-slate-900">{USER_TYPE_LABELS[user.user_type]}</dd>
        <dt className="text-slate-500">Client</dt>
        <dd className="text-slate-900">{user.client ? user.client.name : "All clients"}</dd>
      </dl>

      <section className="mt-8 max-w-2xl rounded-2xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-semibold text-slate-900">Change password</h2>
        <p className="mt-1 text-sm text-slate-600">
          At least {PASSWORD_MIN_LENGTH} characters. Changing it signs you out in every other
          browser; this one stays signed in.
        </p>

        <form onSubmit={handleSubmit} className="mt-5 space-y-4" noValidate>
          <div>
            <label htmlFor="current-password" className="mb-1.5 block text-sm font-medium text-slate-700">
              Current password
            </label>
            <input
              id="current-password"
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              className={inputClass}
              disabled={saving}
            />
          </div>
          <div>
            <label htmlFor="new-password" className="mb-1.5 block text-sm font-medium text-slate-700">
              New password
            </label>
            <input
              id="new-password"
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              className={inputClass}
              disabled={saving}
            />
          </div>
          <div>
            <label htmlFor="confirm-password" className="mb-1.5 block text-sm font-medium text-slate-700">
              Confirm new password
            </label>
            <input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              className={inputClass}
              disabled={saving}
            />
          </div>

          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
          {success && !error && (
            <p className="text-sm text-green-700" role="status">
              Password changed. Your other browsers have been signed out.
            </p>
          )}

          <button
            type="submit"
            disabled={saving}
            className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving..." : "Change password"}
          </button>
        </form>
      </section>
    </div>
  );
}
