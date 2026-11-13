"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { canAccess } from "@/lib/admin-permissions";
import { headerContrast, isValidHex, MIN_CONTRAST, textColorFor } from "@/lib/contrast";
import {
  getWidgetAdmin,
  removeWidgetLogo,
  saveWidgetBranding,
  setWidgetStatus,
  uploadWidgetLogo,
  verifyWidgetInstallation,
  WidgetAdmin,
  WidgetCheck,
  WidgetCheckCode,
} from "@/lib/widget-admin-api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const DEFAULT_HEADER = "#1D4ED8";
const MAX_LOGO_BYTES = 256 * 1024;
const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"];

const CHECK_TONE: Record<WidgetCheckCode, string> = {
  OK: "border-green-200 bg-green-50 text-green-900",
  SNIPPET_NOT_FOUND: "border-amber-200 bg-amber-50 text-amber-900",
  WRONG_SOURCE: "border-amber-200 bg-amber-50 text-amber-900",
  INVALID_URL: "border-red-200 bg-red-50 text-red-900",
  NOT_ALLOWED_HOST: "border-red-200 bg-red-50 text-red-900",
  BLOCKED_ADDRESS: "border-red-200 bg-red-50 text-red-900",
  FETCH_FAILED: "border-red-200 bg-red-50 text-red-900",
  REDIRECT_BLOCKED: "border-red-200 bg-red-50 text-red-900",
  TOO_LARGE: "border-red-200 bg-red-50 text-red-900",
};

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function WidgetAdminPanel() {
  const actor = useAdminUser();
  const allowed = canAccess(actor.user_type, "widget_domains");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;

  const [data, setData] = useState<WidgetAdmin | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [botName, setBotName] = useState("");
  const [greeting, setGreeting] = useState("");
  const [headerColor, setHeaderColor] = useState(DEFAULT_HEADER);

  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const logoInput = useRef<HTMLInputElement>(null);

  const [checkUrl, setCheckUrl] = useState("https://");
  const [check, setCheck] = useState<WidgetCheck | null>(null);

  // Load, and reload when the client changes. The form follows the saved values.
  useEffect(() => {
    if (!allowed || needsClient) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getWidgetAdmin(withClient)
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setBotName(value.bot_name ?? "");
        setGreeting(value.greeting ?? "");
        setHeaderColor(value.header_color);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err, "Could not load the widget settings."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // scope.scope is the only input that changes the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, needsClient, scope.scope]);

  // The preview of a file the admin has picked, before it is uploaded.
  useEffect(() => {
    if (!logoFile) {
      setLogoPreview(null);
      return;
    }
    const url = URL.createObjectURL(logoFile);
    setLogoPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [logoFile]);

  if (!allowed) {
    return null;
  }

  async function run(action: () => Promise<WidgetAdmin | void>, message: string) {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const next = await action();
      if (next) {
        setData(next);
      }
      setSuccess(message);
    } catch (err) {
      setError(errorText(err, "That did not work. Try again."));
    } finally {
      setBusy(false);
    }
  }

  const headerValid = isValidHex(headerColor) || headerColor.trim() === "";
  const effectiveHeader = isValidHex(headerColor) ? headerColor : DEFAULT_HEADER;
  const contrast = headerContrast(effectiveHeader);
  const contrastOk = contrast !== null && contrast.ratio >= MIN_CONTRAST;
  const saveDisabled = busy || !headerValid || !contrastOk;

  function handleSave(event: FormEvent) {
    event.preventDefault();
    if (saveDisabled) return;
    void run(
      () =>
        saveWidgetBranding(withClient, {
          bot_name: botName.trim(),
          greeting: greeting.trim(),
          header_color: headerColor.trim(),
        }),
      "Look saved.",
    );
  }

  function handleLogoPick(file: File | undefined) {
    setError(null);
    setSuccess(null);
    if (!file) {
      setLogoFile(null);
      return;
    }
    if (!LOGO_TYPES.includes(file.type)) {
      setError("Use a PNG, JPEG, or WebP image. SVG files are not accepted.");
      setLogoFile(null);
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setError("The logo must be 256 KB or smaller.");
      setLogoFile(null);
      return;
    }
    setLogoFile(file);
  }

  function uploadLogo() {
    if (!logoFile) return;
    void run(async () => {
      const next = await uploadWidgetLogo(withClient, logoFile);
      setLogoFile(null);
      if (logoInput.current) logoInput.current.value = "";
      return next;
    }, "Logo saved.");
  }

  function removeLogo() {
    if (!window.confirm("Remove the logo? The widget will show the default picture.")) return;
    void run(() => removeWidgetLogo(withClient), "Logo removed.");
  }

  function toggleStatus() {
    if (!data) return;
    if (data.status === "live") {
      const ok = window.confirm(
        "Pause the chat? Visitors will see that the assistant is not available, and no new " +
          "conversations will start. Chats already with your staff keep going.",
      );
      if (!ok) return;
    }
    const next = data.status === "live" ? "paused" : "live";
    void run(() => setWidgetStatus(withClient, next), next === "live" ? "The chat is live." : "The chat is paused.");
  }

  async function copySnippet() {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.snippet);
      setSuccess("Code copied.");
    } catch {
      setError("Could not copy. Select the code and copy it by hand.");
    }
  }

  function handleCheck(event: FormEvent) {
    event.preventDefault();
    if (busy || !checkUrl.trim()) return;
    setBusy(true);
    setError(null);
    setCheck(null);
    verifyWidgetInstallation(withClient, checkUrl.trim())
      .then(setCheck)
      .catch((err: unknown) => setError(errorText(err, "The check could not run.")))
      .finally(() => setBusy(false));
  }

  const previewLogo = logoPreview ?? (data?.logo_url ? `${API_URL}${data.logo_url}` : null);
  const previewName = botName.trim() || "Chat assistant";

  return (
    <div className="space-y-10">
      <ClientPicker scope={scope} id="widget-client" />
      {needsClient ? (
        <p className="text-sm text-slate-500">Choose a client to manage its chat widget.</p>
      ) : loading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : !data ? (
        <p className="text-sm text-red-600" role="alert">
          {error ?? "The widget settings could not be loaded."}
        </p>
      ) : (
        <>
          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
          {success && !error && (
            <p className="text-sm text-green-700" role="status">
              {success}
            </p>
          )}

          {/* Status */}
          <section className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold text-slate-900">Status</h2>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  data.status === "live" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-900"
                }`}
              >
                {data.status === "live" ? "Live" : "Paused"}
              </span>
            </div>
            <p className="max-w-2xl text-sm text-slate-600">
              {data.status === "live"
                ? "Visitors can chat with the assistant on your website."
                : "Visitors see that the assistant is not available. New conversations do not start."}
            </p>
            <button
              type="button"
              onClick={toggleStatus}
              disabled={busy}
              className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-800 disabled:opacity-50"
            >
              {data.status === "live" ? "Pause the chat" : "Go live"}
            </button>
          </section>

          {/* Branding */}
          <section className="grid gap-8 lg:grid-cols-[1fr_320px]">
            <form onSubmit={handleSave} className="max-w-xl space-y-5" noValidate>
              <h2 className="text-lg font-semibold text-slate-900">Look</h2>

              <div>
                <label htmlFor="bot-name" className="block text-sm font-medium text-slate-700">
                  Bot name
                </label>
                <input
                  id="bot-name"
                  value={botName}
                  maxLength={60}
                  onChange={(event) => setBotName(event.target.value)}
                  placeholder="Ava"
                  className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2 text-sm"
                />
                <p className="mt-1 text-xs text-slate-500">Up to 60 characters. Leave blank to use the practice name.</p>
              </div>

              <div>
                <label htmlFor="greeting" className="block text-sm font-medium text-slate-700">
                  Greeting
                </label>
                <textarea
                  id="greeting"
                  value={greeting}
                  maxLength={200}
                  rows={2}
                  onChange={(event) => setGreeting(event.target.value)}
                  placeholder="Hi! How can I help you today?"
                  className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2 text-sm"
                />
                <p className="mt-1 text-xs text-slate-500">Up to 200 characters. Leave blank for the default greeting.</p>
              </div>

              <div>
                <p className="text-sm font-medium text-slate-700">Header color</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-3">
                  <input
                    type="color"
                    aria-label="Header color picker"
                    value={isValidHex(headerColor) ? headerColor : DEFAULT_HEADER}
                    onChange={(event) => setHeaderColor(event.target.value.toUpperCase())}
                    className="h-10 w-14 cursor-pointer rounded border border-slate-300 bg-white p-1"
                  />
                  <input
                    aria-label="Header color hex"
                    value={headerColor}
                    onChange={(event) => setHeaderColor(event.target.value)}
                    placeholder={DEFAULT_HEADER}
                    className="w-32 rounded-xl border border-slate-300 px-3 py-2 font-mono text-sm"
                  />
                  <ContrastBadge ratio={contrast?.ratio ?? null} textColor={contrast?.textColor ?? null} />
                </div>
                {!headerValid && (
                  <p className="mt-1 text-xs text-red-600">Use a color like #1D4ED8.</p>
                )}
                <p className="mt-1 text-xs text-slate-500">
                  The header text is set to white or near-black automatically, whichever reads better. A color
                  that cannot reach 4.5:1 with either is not saved. Leave blank for the default blue.
                </p>
              </div>

              <button
                type="submit"
                disabled={saveDisabled}
                className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? "Saving..." : "Save look"}
              </button>
            </form>

            <div className="space-y-6">
              <div>
                <p className="text-sm font-medium text-slate-700">Preview</p>
                <div className="mt-2 w-full max-w-[320px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                  <div
                    className="flex items-center gap-3 px-4 py-3"
                    style={{
                      backgroundColor: effectiveHeader,
                      color: contrast?.textColor ?? "#FFFFFF",
                    }}
                  >
                    <img
                      src={previewLogo ?? "/bot-profile.png"}
                      alt=""
                      className="h-11 w-11 shrink-0 rounded-full border border-white bg-white object-contain p-0.5"
                    />
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-[15px] font-bold leading-tight">{previewName}</span>
                      <span className="text-xs leading-tight">AI Assistant</span>
                    </div>
                  </div>
                  <div className="space-y-2 p-4">
                    <div className="rounded-2xl bg-slate-100 px-3.5 py-2 text-sm text-slate-900">
                      {greeting.trim() || "Hi! How can I help you today?"}
                    </div>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-sm font-medium text-slate-700">Logo</p>
                <p className="mt-1 text-xs text-slate-500">PNG, JPEG, or WebP. Up to 256 KB. Not SVG.</p>
                <input
                  ref={logoInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => handleLogoPick(event.target.files?.[0])}
                  className="mt-2 block w-full text-sm"
                  disabled={busy}
                />
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={uploadLogo}
                    disabled={busy || !logoFile}
                    className="rounded-xl bg-slate-900 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
                  >
                    Upload logo
                  </button>
                  {data.has_logo && (
                    <button
                      type="button"
                      onClick={removeLogo}
                      disabled={busy}
                      className="rounded-xl border border-slate-300 px-3 py-2 text-xs text-slate-700 disabled:opacity-50"
                    >
                      Remove logo
                    </button>
                  )}
                </div>
              </div>
            </div>
          </section>

          {/* Install */}
          <section className="max-w-3xl space-y-3">
            <h2 className="text-lg font-semibold text-slate-900">Add the chat to your website</h2>
            <p className="text-sm text-slate-600">
              Copy this code and paste it into your website where you want the chat to appear. Most site
              builders accept it in a section labelled Custom HTML or Embed code. Your site must be on the
              list under Allowed websites, or the chat will not show there.
            </p>
            <textarea
              readOnly
              value={data.snippet}
              rows={8}
              aria-label="Embed code"
              className="w-full rounded-xl border border-slate-300 bg-slate-50 p-3 font-mono text-xs text-slate-800"
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={copySnippet}
                className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white"
              >
                Copy code
              </button>
              <a href="/admin/settings" className="text-sm text-slate-700 underline">
                Manage allowed websites
              </a>
            </div>
          </section>

          {/* Verify */}
          <section className="max-w-3xl space-y-3">
            <h2 className="text-lg font-semibold text-slate-900">Check the installation</h2>
            <p className="text-sm text-slate-600">
              Enter the address of a page that has the code. The check reads the page HTML as a server would.
              It cannot see a chat that a script adds after the page loads. For those pages, open the page
              yourself and check. You can run this up to 10 times an hour.
            </p>
            <form onSubmit={handleCheck} className="flex flex-wrap gap-2" noValidate>
              <label htmlFor="check-url" className="sr-only">
                Page address
              </label>
              <input
                id="check-url"
                type="url"
                value={checkUrl}
                onChange={(event) => setCheckUrl(event.target.value)}
                placeholder="https://yourpractice.com/"
                className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-sm"
                disabled={busy}
              />
              <button
                type="submit"
                disabled={busy || !checkUrl.trim()}
                className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {busy ? "Checking..." : "Check page"}
              </button>
            </form>
            {check && (
              <div
                role="status"
                className={`rounded-xl border px-4 py-3 text-sm ${CHECK_TONE[check.code]}`}
              >
                <p className="font-medium">{check.message}</p>
                <p className="mt-1 text-xs opacity-80">{check.note}</p>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function ContrastBadge({ ratio, textColor }: { ratio: number | null; textColor: string | null }) {
  if (ratio === null) {
    return <span className="text-xs text-slate-500">Enter a full color</span>;
  }
  const passes = ratio >= MIN_CONTRAST;
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-medium ${
        passes ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
      }`}
      title={`Header text: ${textColor ?? textColorFor(DEFAULT_HEADER)}`}
    >
      {ratio.toFixed(2)}:1 {passes ? "passes AA" : "below 4.5:1"}
    </span>
  );
}
