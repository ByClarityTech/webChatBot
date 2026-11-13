"use client";

import { useEffect, useState } from "react";

import { ConversationDetail, getConversation } from "@/lib/admin-analytics-api";
import { ApiError } from "@/lib/admin-api";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : "The transcript could not be loaded.";
}

const ROLE_LABEL = { visitor: "Visitor", assistant: "Assistant" } as const;

/** A side panel with one conversation. Opening it has already been recorded by the server. */
export function ConversationDrawer({
  sessionId,
  clientId,
  onClose,
}: {
  sessionId: string;
  clientId?: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    getConversation(sessionId, clientId)
      .then((loaded) => {
        if (!cancelled) {
          setDetail(loaded);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(errorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, clientId]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="transcript-heading"
        className="flex h-full w-full max-w-2xl flex-col overflow-y-auto bg-white p-6 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id="transcript-heading" className="text-lg font-semibold text-slate-900">
            Conversation
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
          >
            Close
          </button>
        </div>

        <div role="note" className="mt-4 rounded-lg border border-amber-600 bg-amber-50 px-4 py-3 text-sm text-amber-950">
          <p className="font-semibold">This view is recorded.</p>
          <p className="mt-1">
            Opening a transcript is written to the audit log. Visitor messages can contain personal
            health details. Do not copy them outside the practice&apos;s systems.
          </p>
        </div>

        {error ? (
          <p role="alert" className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">{error}</p>
        ) : null}
        {!error && !detail ? <p className="mt-4 text-sm text-slate-500">Loading...</p> : null}

        {detail ? (
          <div className="mt-4 space-y-6">
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs font-medium text-slate-600">Started</dt>
                <dd className="text-slate-900">{new Date(detail.started_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-slate-600">Outcome</dt>
                <dd className="text-slate-900">
                  {detail.status === "in_progress"
                    ? "In progress"
                    : (detail.outcome_label ?? "Not labeled yet")}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs font-medium text-slate-600">Why</dt>
                <dd className="text-slate-900">{detail.reason ?? "Set when the conversation ends"}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-slate-600">Visitor messages</dt>
                <dd className="tabular-nums text-slate-900">{detail.visitor_messages ?? "-"}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-slate-600">Length</dt>
                <dd className="tabular-nums text-slate-900">
                  {detail.duration_seconds === null ? "-" : `${Math.round(detail.duration_seconds / 60)} min`}
                </dd>
              </div>
              {detail.contact ? (
                <div className="sm:col-span-2">
                  <dt className="text-xs font-medium text-slate-600">Contact shared in chat</dt>
                  <dd className="text-slate-900">
                    {detail.contact.name}
                    {detail.contact.masked_phone ? `, ${detail.contact.masked_phone}` : ""}
                  </dd>
                </div>
              ) : null}
            </dl>

            <section aria-labelledby="unanswered-heading">
              <h3 id="unanswered-heading" className="text-sm font-semibold text-slate-900">
                Questions the knowledge base could not answer
              </h3>
              {detail.unanswered.length === 0 ? (
                <p className="mt-2 text-sm text-slate-600">None in this conversation.</p>
              ) : (
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-800">
                  {detail.unanswered.map((item, index) => (
                    <li key={`${item.asked_at}-${index}`}>
                      {item.question}{" "}
                      <span className="text-xs text-slate-500">({new Date(item.asked_at).toLocaleTimeString()})</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section aria-labelledby="transcript-section">
              <h3 id="transcript-section" className="text-sm font-semibold text-slate-900">Messages</h3>
              {detail.transcript.length === 0 ? (
                <p className="mt-2 text-sm text-slate-600">No messages were saved for this conversation.</p>
              ) : (
                <ol className="mt-2 space-y-3">
                  {detail.transcript.map((line) => (
                    <li key={line.position} className="rounded-lg border border-slate-200 p-3">
                      <p className="text-xs font-semibold text-slate-600">{ROLE_LABEL[line.role]}</p>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-900">{line.text}</p>
                    </li>
                  ))}
                </ol>
              )}
              <p className="mt-2 text-xs text-slate-500">
                Messages are in order. The saved conversation has no times for them.
              </p>
            </section>

            {detail.staff_messages.length > 0 ? (
              <section aria-labelledby="staff-heading">
                <h3 id="staff-heading" className="text-sm font-semibold text-slate-900">Live chat with staff</h3>
                <ol className="mt-2 space-y-3">
                  {detail.staff_messages.map((message, index) => (
                    <li key={`${message.created_at}-${index}`} className="rounded-lg border border-slate-200 p-3">
                      <p className="text-xs font-semibold text-slate-600">
                        {message.sender === "staff" ? "Staff" : "Visitor"} at{" "}
                        {new Date(message.created_at).toLocaleString()}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-900">{message.body}</p>
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}
          </div>
        ) : null}
      </aside>
    </div>
  );
}
