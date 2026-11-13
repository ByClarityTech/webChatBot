"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { adminLogin, ApiError, getCurrentUser } from "@/lib/admin-api";

function loginErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      const minutes = err.retryAfterSeconds ? Math.ceil(err.retryAfterSeconds / 60) : null;
      return minutes
        ? `Too many failed attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`
        : "Too many failed attempts. Try again later.";
    }
    if (err.status === 401) {
      return "Invalid email or password.";
    }
    return err.message;
  }
  return "Could not reach the server. Check that the API is running.";
}

export default function AdminLoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired")) {
      setNotice("Your session ended. Please sign in again.");
    } else if (params.get("notice") === "password-set") {
      setNotice("Your password is saved. Sign in with it now.");
    }
    // Already signed in: go straight to the dashboard.
    getCurrentUser()
      .then((user) => {
        if (user) {
          router.replace("/admin");
        }
      })
      .catch(() => {
        // API unreachable; the form shows the error on submit.
      });
  }, [router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }

    setLoading(true);
    setError(null);
    setNotice(null);

    try {
      await adminLogin(email.trim(), password);
      router.replace("/admin");
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">Admin sign in</h1>
        <p className="mt-2 text-sm text-slate-600">Sign in with your work email and password.</p>

        {notice && (
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {notice}
          </p>
        )}

        <form onSubmit={handleSubmit} className="mt-6 space-y-4" noValidate>
          <div>
            <label htmlFor="email" className="mb-1.5 block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
              disabled={loading}
            />
          </div>

          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <label htmlFor="password" className="block text-sm font-medium text-slate-700">
                Password
              </label>
              <Link href="/auth/forgot-password" className="text-xs text-slate-600 underline">
                Forgot password?
              </Link>
            </div>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
              disabled={loading}
            />
          </div>

          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || !email.trim() || !password}
            className="w-full rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </main>
  );
}
