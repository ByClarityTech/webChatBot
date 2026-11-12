"use client";

import { useCallback, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { draftGap, dismissGap, FaqGap, listGaps } from "@/lib/faq-gaps-api";
import { canAccess } from "@/lib/admin-permissions";

const REASON_LABELS: Record<FaqGap["reason"], string> = {
  no_match: "Nothing in the knowledge base matched",
  model_declined: "The assistant could not answer from the knowledge base",
  keyword_miss: "No approved FAQ matched while the assistant was unavailable",
};

const actionClass =
  "rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50";

/** The most-asked questions the chatbot could not answer. A person drafts or dismisses each. */
export function FaqGapsPanel() {
  const actor = useAdminUser();
  const allowed = canAccess(actor.user_type, "documents");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;

  const [gaps, setGaps] = useState<FaqGap[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(async () => {
    setGaps(await listGaps(withClient));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- withClient changes with the scope
  }, [scope.scope]);

  useEffect(() => {
    if (!allowed || needsClient) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    load()
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load questions.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, needsClient, load]);

  if (!allowed) {
    return null;
  }

  async function act(gap: FaqGap, action: "draft" | "dismiss") {
    setBusyId(gap.id);
    setError(null);
    setSuccess(null);
    try {
      if (action === "draft") {
        await draftGap(withClient, gap.id);
        setSuccess("Draft created. Write the answer in the FAQs below, then approve it.");
      } else {
        await dismissGap(withClient, gap.id);
        setSuccess("Question dismissed.");
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work. Try again.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Top unanswered questions</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Questions visitors asked that the chatbot could not answer. Draft one to start its FAQ,
            then write the answer yourself. Nothing is written for you.
          </p>
        </div>
        <ClientPicker scope={scope} id="gaps-client" />
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="mt-4 text-sm text-green-700" role="status">
          {success}
        </p>
      )}

      {needsClient ? (
        <p className="mt-4 text-sm text-slate-500">Choose a client to see its unanswered questions.</p>
      ) : loading ? (
        <p className="mt-4 text-sm text-slate-500">Loading questions...</p>
      ) : gaps.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">No open unanswered questions.</p>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100">
          {gaps.map((gap) => (
            <li key={gap.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="break-words text-sm font-medium text-slate-900">{gap.question}</p>
                <p className="mt-1 text-xs text-slate-500">
                  Asked {gap.ask_count} time{gap.ask_count === 1 ? "" : "s"} · {REASON_LABELS[gap.reason]}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  disabled={busyId === gap.id}
                  onClick={() => act(gap, "draft")}
                  className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Draft FAQ
                </button>
                <button
                  type="button"
                  disabled={busyId === gap.id}
                  onClick={() => act(gap, "dismiss")}
                  className={actionClass}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
