"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import {
  AuthCard,
  Banner,
  inputClass,
  primaryButtonClass,
  Spinner,
} from "@/components/auth/auth-card";
import { ApiError, LOGIN_PATH, requestPasswordReset } from "@/lib/admin-api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const trimmed = email.trim();
  const emailReason = trimmed ? null : "Enter the email you sign in with.";
  const canSubmit = !emailReason && !saving && !message;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      // The reply is the same whether or not an account matches, so it is shown as given.
      setMessage(await requestPasswordReset(trimmed));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the server. Check your connection and try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <AuthCard
      title="Forgot your password?"
      subtitle="Enter the email you use to sign in. If an account exists, we will send a link that works once and expires in one hour."
      footer={
        <Link href={LOGIN_PATH} className="font-medium text-slate-600 transition hover:text-[#1D4ED8]">
          Back to sign in
        </Link>
      }
    >
      {message ? (
        <Banner tone="success">{message}</Banner>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-5" noValidate>
          {error && <Banner tone="error">{error}</Banner>}

          <div>
            <label htmlFor="email" className="mb-1.5 block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setError(null);
              }}
              autoComplete="username"
              autoFocus
              disabled={saving}
              className={inputClass}
            />
          </div>

          <div className="space-y-2">
            <button type="submit" disabled={!canSubmit} className={primaryButtonClass}>
              {saving && <Spinner />}
              {saving ? "Sending..." : "Send reset link"}
            </button>
            {emailReason && !saving && (
              <p className="text-center text-xs text-slate-500" aria-live="polite">
                {emailReason}
              </p>
            )}
          </div>
        </form>
      )}
    </AuthCard>
  );
}
