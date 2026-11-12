"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { useAdminUser } from "@/components/admin/admin-session";
import { ApiError, getAllowedDomains, saveAllowedDomains } from "@/lib/admin-api";
import { canAccess } from "@/lib/admin-permissions";

// Mirrors backend/app/db/models/client.py. The API checks again and returns its own message.
const MAX_ENTRIES = 50;
const PATTERN = /^(?:\*\.[a-z0-9-]+(?:\.[a-z0-9-]+)+|[a-z0-9-]+(?:\.[a-z0-9-]+)*)(?::(\d{1,5}))?$/;
const SCHEME = /^[a-z][a-z0-9+.-]*:\/\//;
const DEFAULT_PORTS = new Set(["80", "443"]);
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1"]);

/** What a person typed, reduced to a host pattern: no scheme, path, query, or default port. */
export function cleanEntry(raw: string): string {
  let value = raw.trim().toLowerCase().replace(SCHEME, "");
  value = value.split(/[/?#]/)[0].replace(/\.+$/, "");
  const colon = value.lastIndexOf(":");
  if (colon >= 0 && DEFAULT_PORTS.has(value.slice(colon + 1))) {
    value = value.slice(0, colon);
  }
  return value;
}

/** Why a cleaned entry is not allowed, or null when it is fine. */
export function entryProblem(cleaned: string): string | null {
  const match = PATTERN.exec(cleaned);
  if (!match) {
    return "Use a site name such as clinic.com, *.clinic.com or localhost:3000.";
  }
  if (match[1] !== undefined) {
    const port = Number(match[1]);
    if (port < 1 || port > 65535) {
      return "The port must be between 1 and 65535.";
    }
  }
  return null;
}

type LineProblem = { line: number; entry: string; message: string };

/** Splits the textarea into entries (one per line, or separated by commas). */
function parseEntries(text: string): { raw: string; cleaned: string }[] {
  return text
    .split(/[\n,]+/)
    .map((raw) => ({ raw: raw.trim(), cleaned: cleanEntry(raw) }))
    .filter((entry) => entry.raw !== "");
}

function isLocal(cleaned: string): boolean {
  const host = cleaned.split(":")[0];
  return LOCAL_HOSTS.has(host);
}

const textareaClass =
  "w-full rounded-xl border border-slate-300 px-3 py-2 font-mono text-sm outline-none ring-slate-400 focus:ring-2";

export function AllowedDomainsForm() {
  const actor = useAdminUser();
  const allowed = canAccess(actor.user_type, "widget_domains");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;

  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const entries = useMemo(() => parseEntries(text), [text]);
  const problems: LineProblem[] = useMemo(() => {
    const found: LineProblem[] = [];
    const lines = text.split(/\n/);
    entries.forEach((entry) => {
      const message = entryProblem(entry.cleaned);
      if (message) {
        // Report against the line the person typed, when it can be found.
        const line = lines.findIndex((l) => l.includes(entry.raw)) + 1;
        found.push({ line: Math.max(line, 1), entry: entry.raw, message });
      }
    });
    if (entries.length > MAX_ENTRIES) {
      found.push({
        line: 0,
        entry: "",
        message: `At most ${MAX_ENTRIES} sites. Remove ${entries.length - MAX_ENTRIES}.`,
      });
    }
    return found;
  }, [entries, text]);

  const localEntries = entries.filter((entry) => isLocal(entry.cleaned)).map((entry) => entry.cleaned);
  const uniqueCount = new Set(entries.map((entry) => entry.cleaned)).size;
  const hasProblems = problems.length > 0;

  useEffect(() => {
    if (!allowed) {
      return;
    }
    if (needsClient) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    getAllowedDomains(scope.scope)
      .then((data) => {
        if (!cancelled) {
          setText(data.domains.join("\n"));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Could not load the allowed sites.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // scope.scope is the only input that changes the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, needsClient, scope.scope]);

  if (!allowed) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hasProblems || saving) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSuccess(null);
    try {
      const saved = await saveAllowedDomains(
        Array.from(new Set(entries.map((entry) => entry.cleaned))),
        scope.scope,
      );
      setText(saved.domains.join("\n"));
      setSuccess("Allowed sites saved.");
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Could not save the allowed sites.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Allowed websites</h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          The chat widget only runs on these sites. One site per line, such as{" "}
          <code className="text-xs">clinic.com</code> or <code className="text-xs">*.clinic.com</code>{" "}
          for every subdomain. Protocols, paths, and default ports are removed for you.
        </p>
      </div>

      <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
        Chat widget fails closed if no domains are allowed in production.
      </p>

      <div className="max-w-xl">
        <ClientPicker scope={scope} id="domains-client" />
      </div>

      {needsClient ? (
        <p className="text-sm text-slate-500">Choose a client to see and edit its allowed sites.</p>
      ) : loading ? (
        <p className="text-sm text-slate-500">Loading allowed sites...</p>
      ) : (
        <form onSubmit={handleSubmit} className="max-w-xl space-y-4" noValidate>
          <div>
            <label htmlFor="allowed-domains" className="block text-sm font-medium text-slate-700">
              Sites
            </label>
            <textarea
              id="allowed-domains"
              rows={6}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setSuccess(null);
                setSaveError(null);
              }}
              placeholder={"clinic.com\n*.clinic.com\nlocalhost:3000"}
              aria-invalid={hasProblems || undefined}
              aria-describedby="allowed-domains-hint"
              className={`${textareaClass} mt-1.5 ${hasProblems ? "border-red-400" : ""}`}
              disabled={saving}
            />
            <p id="allowed-domains-hint" className="mt-1.5 text-xs text-slate-500">
              {uniqueCount} site{uniqueCount === 1 ? "" : "s"} · up to {MAX_ENTRIES}
            </p>
          </div>

          {hasProblems && (
            <ul className="space-y-1 text-sm text-red-600" role="alert">
              {problems.map((problem, index) => (
                <li key={`${problem.line}-${index}`}>
                  {problem.line > 0 ? `Line ${problem.line}: ` : ""}
                  {problem.entry ? `"${problem.entry}" ` : ""}
                  {problem.message}
                </li>
              ))}
            </ul>
          )}

          {localEntries.length > 0 && !hasProblems && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {Array.from(new Set(localEntries)).join(", ")} is allowed for local staging. Remove it
              before the site goes live.
            </p>
          )}

          {loadError && (
            <p className="text-sm text-red-600" role="alert">
              {loadError}
            </p>
          )}
          {saveError && (
            <p className="text-sm text-red-600" role="alert">
              {saveError}
            </p>
          )}
          {success && !saveError && (
            <p className="text-sm text-green-700" role="status">
              {success}
            </p>
          )}

          <button
            type="submit"
            disabled={saving || hasProblems || Boolean(loadError)}
            className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save allowed sites"}
          </button>
        </form>
      )}
    </section>
  );
}
