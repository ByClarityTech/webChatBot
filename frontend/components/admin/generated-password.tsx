"use client";

import { useState } from "react";

type GeneratedPasswordProps = {
  password: string;
  /** Who the password is for, e.g. the user's email. */
  forLabel: string;
  onDone: () => void;
};

/**
 * Shows an admin-set password exactly once. It is not stored in the page after onDone, so
 * the admin must copy it now and pass it to the user privately.
 */
export function GeneratedPassword({ password, forLabel, onDone }: GeneratedPasswordProps) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopyFailed(true);
    }
  }

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4" role="status">
      <p className="text-sm font-medium text-amber-900">Password for {forLabel}</p>
      <p className="mt-1 text-xs text-amber-800">
        Shown only once. Copy it now and give it to the user privately. They must choose their
        own password the first time they sign in.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="select-all rounded-lg border border-amber-200 bg-white px-3 py-2 font-mono text-sm text-slate-900">
          {password}
        </code>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Done
        </button>
      </div>
      {copyFailed && (
        <p className="mt-2 text-xs text-red-700">
          Copy was blocked by the browser. Select the password above and copy it manually.
        </p>
      )}
    </div>
  );
}
