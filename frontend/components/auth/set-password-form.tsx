"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";

import {
  AuthCard,
  Banner,
  primaryButtonClass,
  Spinner,
} from "@/components/auth/auth-card";
import { PasswordField } from "@/components/auth/password-field";
import { ApiError, LOGIN_PATH, resetPassword, setupPassword } from "@/lib/admin-api";
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/passwords";

export type PasswordLinkMode = "setup" | "reset";

const COPY: Record<
  PasswordLinkMode,
  {
    title: string;
    subtitle: string;
    submitLabel: string;
    submit: typeof setupPassword;
  }
> = {
  setup: {
    title: "Set your password",
    subtitle: "Choose a password to finish setting up your admin account.",
    submitLabel: "Save password",
    submit: setupPassword,
  },
  reset: {
    title: "Choose a new password",
    subtitle: `Choose a new password for your admin account. Your other sign-ins will end. At least ${PASSWORD_MIN_LENGTH} characters.`,
    submitLabel: "Save new password",
    submit: resetPassword,
  },
};

const MISSING_TOKEN = "Missing or invalid invite token in URL. Open the link from your email again.";

function submitErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return "Could not reach the server. Check your connection and try again.";
}

/** The first thing stopping submit, in the order a person would fix it. */
function blockingReason(token: string, password: string, confirm: string): string | null {
  if (!token) {
    return MISSING_TOKEN;
  }
  if (!password) {
    return "Enter a new password.";
  }
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
  }
  if (password.length > PASSWORD_MAX_LENGTH) {
    return `Use at most ${PASSWORD_MAX_LENGTH} characters.`;
  }
  if (!confirm) {
    return "Confirm your password.";
  }
  if (password !== confirm) {
    return "Passwords do not match.";
  }
  return null;
}

function Requirement({ met, children }: { met: boolean; children: React.ReactNode }) {
  return (
    <li className={`flex items-center gap-2 text-xs ${met ? "text-green-700" : "text-slate-500"}`}>
      <span
        aria-hidden
        className={`flex h-4 w-4 items-center justify-center rounded-full ${
          met ? "bg-green-100" : "bg-slate-100"
        }`}
      >
        {met ? (
          <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M2.5 6.2l2.2 2.2 4.8-4.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
        )}
      </span>
      {children}
    </li>
  );
}

export function PasswordLinkForm({ mode }: { mode: PasswordLinkMode }) {
  const router = useRouter();
  const token = useSearchParams().get("token")?.trim() ?? "";
  const copy = COPY[mode];

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // Separate Show/Hide state for each field; both start hidden.
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const lengthOk =
    newPassword.length >= PASSWORD_MIN_LENGTH && newPassword.length <= PASSWORD_MAX_LENGTH;
  const matchOk = confirmPassword.length > 0 && newPassword === confirmPassword;
  const reason = blockingReason(token, newPassword, confirmPassword);
  const canSubmit = reason === null && !saving && !done;

  // Field errors appear only after something is typed, so a fresh form is not all red.
  const lengthError =
    newPassword.length > 0 && !lengthOk
      ? newPassword.length < PASSWORD_MIN_LENGTH
        ? `Must be at least ${PASSWORD_MIN_LENGTH} characters`
        : `Must be at most ${PASSWORD_MAX_LENGTH} characters`
      : null;
  const matchError = confirmPassword.length > 0 && !matchOk ? "Passwords do not match" : null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await copy.submit(token, newPassword);
      setDone(true);
      router.replace(`${LOGIN_PATH}?notice=password-set`);
    } catch (err) {
      setError(submitErrorMessage(err));
      setSaving(false);
    }
  }

  const expired = error !== null && /invalid or has expired/i.test(error);

  return (
    <AuthCard
      title={copy.title}
      subtitle={copy.subtitle}
      footer={
        <Link href={LOGIN_PATH} className="font-medium text-slate-600 transition hover:text-[#1D4ED8]">
          Back to sign in
        </Link>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5" noValidate>
        {!token && <Banner tone="error">{MISSING_TOKEN}</Banner>}

        {error && (
          <Banner tone="error">
            <p>{error}</p>
            {expired && (
              <p className="mt-1">
                {mode === "setup" ? (
                  "Ask your administrator to send a new invite."
                ) : (
                  <Link href="/auth/forgot-password" className="font-medium underline">
                    Request a new reset link
                  </Link>
                )}
              </p>
            )}
          </Banner>
        )}

        <PasswordField
          id="new-password"
          label="New password"
          value={newPassword}
          onChange={(value) => {
            setNewPassword(value);
            setError(null);
          }}
          autoComplete="new-password"
          visible={showNewPassword}
          onToggleVisible={() => setShowNewPassword((prev) => !prev)}
          autoFocus={Boolean(token)}
          disabled={saving || done}
          error={lengthError}
        />

        <PasswordField
          id="confirm-password"
          label="Confirm password"
          value={confirmPassword}
          onChange={(value) => {
            setConfirmPassword(value);
            setError(null);
          }}
          autoComplete="new-password"
          visible={showConfirmPassword}
          onToggleVisible={() => setShowConfirmPassword((prev) => !prev)}
          disabled={saving || done}
          error={matchError}
        />

        <ul className="space-y-1.5" aria-label="Password requirements">
          <Requirement met={lengthOk}>At least {PASSWORD_MIN_LENGTH} characters</Requirement>
          <Requirement met={matchOk}>Passwords match</Requirement>
        </ul>

        <div className="space-y-2">
          <button type="submit" disabled={!canSubmit} className={primaryButtonClass}>
            {saving && <Spinner />}
            {saving ? "Saving..." : copy.submitLabel}
          </button>
          {/* Without a token the banner above already says why; do not repeat it. */}
          {reason && token && !saving && !done && (
            <p className="text-center text-xs text-slate-500" aria-live="polite">
              {reason}
            </p>
          )}
        </div>
      </form>
    </AuthCard>
  );
}
