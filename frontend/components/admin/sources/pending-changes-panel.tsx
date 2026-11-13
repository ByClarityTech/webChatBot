"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/admin-api";
import {
  approveChange,
  ChangeType,
  getPendingChange,
  listPendingChanges,
  PendingChange,
  PendingChangeDetail,
  rejectChange,
} from "@/lib/admin-sources-api";

const CHANGE_LABELS: Record<ChangeType, string> = {
  new: "New",
  changed: "Changed",
  removed: "Removed",
};

// Each badge has a text label and its own border, so the type is not shown by colour alone.
const BADGE_CLASSES: Record<ChangeType, string> = {
  new: "border-sky-700 bg-sky-50 text-sky-900",
  changed: "border-amber-700 bg-amber-50 text-amber-900",
  removed: "border-rose-700 bg-rose-50 text-rose-900",
};

const buttonClass =
  "rounded-lg border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

export function ChangeBadge({ type }: { type: ChangeType }) {
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${BADGE_CLASSES[type]}`}
    >
      {CHANGE_LABELS[type]}
    </span>
  );
}

export function WordCounts({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="font-mono text-xs text-slate-700">
      +{added} / -{removed} words
    </span>
  );
}

/** Inline word diff: additions underlined, removals struck through, both with text labels. */
export function DiffView({ detail }: { detail: PendingChangeDetail }) {
  const hasText = detail.segments.some((segment) => segment.text.length > 0);
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-slate-600">
        Text with a line through it is removed. Underlined text is added. Unmarked text stays.
      </p>
      <div
        className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-white p-4 text-sm leading-relaxed text-slate-900"
        aria-label="Word-level changes"
      >
        {hasText ? (
          detail.segments.map((segment, index) => {
            if (segment.op === "insert") {
              return (
                <ins key={index} className="bg-emerald-50 text-emerald-900 underline">
                  {segment.text}{" "}
                </ins>
              );
            }
            if (segment.op === "delete") {
              return (
                <del key={index} className="bg-rose-50 text-rose-900 line-through">
                  {segment.text}{" "}
                </del>
              );
            }
            return <span key={index}>{segment.text} </span>;
          })
        ) : (
          <span className="text-slate-500">No text on this page.</span>
        )}
      </div>
    </div>
  );
}

export function PendingChangesPanel({
  clientId,
  refreshKey,
  onDecided,
}: {
  clientId?: string;
  refreshKey: number;
  onDecided: () => void;
}) {
  const [changes, setChanges] = useState<PendingChange[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PendingChangeDetail | null>(null);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    const list = await listPendingChanges(clientId);
    setChanges(list);
    setSelectedId((current) =>
      current && list.some((change) => change.id === current) ? current : null,
    );
  }, [clientId]);

  // Reloads when the page's refresh key changes (a crawl finished or was polled); the open
  // change stays selected if it is still waiting.
  useEffect(() => {
    setError(null);
    loadList().catch((err) => setError(errorMessage(err, "Could not load pending changes.")));
  }, [loadList, refreshKey]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    getPendingChange(selectedId, clientId)
      .then((loaded) => {
        if (!cancelled) {
          setDetail(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(errorMessage(err, "Could not load this change."));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, clientId]);

  async function decide(action: "approve" | "reject") {
    if (!detail) {
      return;
    }
    setBusy(action);
    setError(null);
    setMessage(null);
    try {
      const run = action === "approve" ? approveChange : rejectChange;
      await run(detail.id, clientId);
      setMessage(
        action === "approve"
          ? `Approved. ${detail.page_url} is now used by the chatbot.`
          : `Rejected. ${detail.page_url} will not be proposed again until its text changes.`,
      );
      setSelectedId(null);
      await loadList();
      onDecided();
    } catch (err) {
      setError(errorMessage(err, "The change could not be saved."));
      // A change decided elsewhere or missing: refresh so the list is current.
      if (err instanceof ApiError && (err.status === 404 || err.status === 409)) {
        await loadList().catch(() => undefined);
        setSelectedId(null);
      }
    } finally {
      setBusy(null);
    }
  }

  if (changes === null && !error) {
    return <p className="text-sm text-slate-500">Loading changes...</p>;
  }

  return (
    <div className="space-y-4">
      {message ? (
        <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          {message}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">
          {error}
        </p>
      ) : null}

      {changes && changes.length === 0 ? (
        <p className="text-sm text-slate-600">
          No changes are waiting. The next monthly crawl, or a crawl started now, will propose any
          changes here. Nothing reaches the chatbot until you approve it.
        </p>
      ) : null}

      {changes && changes.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">Changes waiting for review</caption>
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-2">Page</th>
                <th scope="col" className="px-4 py-2">Change</th>
                <th scope="col" className="px-4 py-2">Size</th>
                <th scope="col" className="px-4 py-2">Found</th>
                <th scope="col" className="px-4 py-2">
                  <span className="sr-only">Review</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {changes.map((change) => (
                <tr key={change.id} className={selectedId === change.id ? "bg-slate-50" : ""}>
                  <td className="max-w-xs truncate px-4 py-2 font-medium text-slate-900">
                    {change.page_url}
                  </td>
                  <td className="px-4 py-2">
                    <ChangeBadge type={change.change_type} />
                  </td>
                  <td className="px-4 py-2">
                    <WordCounts added={change.words_added} removed={change.words_removed} />
                  </td>
                  <td className="px-4 py-2 text-slate-600">
                    {new Date(change.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => setSelectedId(change.id)}
                      aria-pressed={selectedId === change.id}
                      className={`${buttonClass} border-slate-300 text-slate-700 hover:bg-slate-50`}
                    >
                      Review
                      <span className="sr-only"> {change.page_url}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {detail ? (
        <section
          aria-labelledby="change-detail-heading"
          className="rounded-xl border border-slate-200 bg-white p-5"
        >
          <div className="flex flex-wrap items-center gap-3">
            <ChangeBadge type={detail.change_type} />
            <h2 id="change-detail-heading" className="text-base font-semibold text-slate-900">
              {detail.page_url}
            </h2>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            {detail.change_type === "removed"
              ? "The chatbot uses this page now. Approving takes it out of the chatbot. Rejecting keeps it."
              : `${detail.live_title ?? "No title"} to ${detail.proposed_title ?? "no title"}. Approving makes this text the one the chatbot uses.`}
          </p>
          <div className="mt-2">
            <WordCounts added={detail.words_added} removed={detail.words_removed} />
          </div>
          <DiffView detail={detail} />
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void decide("approve")}
              disabled={busy !== null}
              className={`${buttonClass} border-emerald-700 bg-emerald-700 text-white hover:bg-emerald-800`}
            >
              {busy === "approve" ? "Approving..." : "Approve"}
            </button>
            <button
              type="button"
              onClick={() => void decide("reject")}
              disabled={busy !== null}
              className={`${buttonClass} border-slate-300 text-slate-700 hover:bg-slate-50`}
            >
              {busy === "reject" ? "Rejecting..." : "Reject"}
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
