"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ConversationDrawer } from "@/components/admin/conversations/conversation-drawer";
import {
  ConversationItem,
  ConversationQuery,
  listConversations,
  listPatientGroups,
  OutcomeKey,
  PatientGroup,
  RangePreset,
} from "@/lib/admin-analytics-api";
import { ApiError, listClients, ManagedClient } from "@/lib/admin-api";
import { canAccess } from "@/lib/admin-permissions";

const PAGE_SIZE = 25;

const OUTCOME_OPTIONS: { value: OutcomeKey | "in_progress" | ""; label: string }[] = [
  { value: "", label: "Any outcome" },
  { value: "booking", label: "Booking" },
  { value: "callback", label: "Callback" },
  { value: "handover", label: "Handover" },
  { value: "resolved", label: "Resolved" },
  { value: "abandoned", label: "Abandoned" },
  { value: "in_progress", label: "In progress" },
];

const selectClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm";
const buttonClass =
  "rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

/** Text labels for the status, so the state is not shown by colour alone. */
export function statusText(item: ConversationItem): string {
  if (item.status === "in_progress") {
    return "In progress";
  }
  return item.outcome_label ?? "Not labeled yet";
}

export function ConversationsView() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";
  // Phone lookups and the patient view identify a person: Admin and Super Admin only.
  const canSeePatients = canAccess(actor.user_type, "conversations");

  const [tab, setTab] = useState<"list" | "patients">("list");
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientId, setClientId] = useState("");
  const [preset, setPreset] = useState<RangePreset>("30d");
  const [outcome, setOutcome] = useState<OutcomeKey | "in_progress" | "">("");
  const [phoneDraft, setPhoneDraft] = useState("");
  const [phone, setPhone] = useState("");
  const [page, setPage] = useState(0);

  const [items, setItems] = useState<ConversationItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [groups, setGroups] = useState<PatientGroup[] | null>(null);
  const [groupTotal, setGroupTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [openSession, setOpenSession] = useState<string | null>(null);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch(() => setClients([]));
  }, [isSuperAdmin]);

  const load = useCallback(async () => {
    // A Super Admin chooses a client first; the server refuses a request without one.
    if (isSuperAdmin && !clientId) {
      return;
    }
    setError(null);
    const base = {
      clientId: clientId || undefined,
      preset,
    };
    try {
      if (tab === "list") {
        const query: ConversationQuery = {
          ...base,
          outcome: outcome || undefined,
          phone: canSeePatients && phone ? phone : undefined,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        };
        const result = await listConversations(query);
        setItems(result.items);
        setTotal(result.total);
      } else {
        const result = await listPatientGroups({
          ...base,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        });
        setGroups(result.groups);
        setGroupTotal(result.total);
      }
    } catch (err) {
      setError(errorMessage(err, "The conversations could not be loaded."));
    }
  }, [tab, clientId, preset, outcome, phone, page, canSeePatients, isSuperAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  function applyPhone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(0);
    setPhone(phoneDraft.trim());
  }

  const count = tab === "list" ? total : groupTotal;
  const lastPage = Math.max(0, Math.ceil(count / PAGE_SIZE) - 1);

  return (
    <div className="space-y-6">
      <div role="tablist" aria-label="Conversation views" className="flex gap-1 border-b border-slate-200">
        <button
          id="tab-list"
          role="tab"
          type="button"
          aria-selected={tab === "list"}
          aria-controls="panel-list"
          onClick={() => {
            setTab("list");
            setPage(0);
          }}
          className={`-mb-px rounded-t-lg border border-b-0 px-4 py-2 text-sm font-medium ${
            tab === "list" ? "border-slate-200 bg-white text-slate-900" : "border-transparent text-slate-600"
          }`}
        >
          All conversations
        </button>
        {canSeePatients ? (
          <button
            id="tab-patients"
            role="tab"
            type="button"
            aria-selected={tab === "patients"}
            aria-controls="panel-patients"
            onClick={() => {
              setTab("patients");
              setPage(0);
            }}
            className={`-mb-px rounded-t-lg border border-b-0 px-4 py-2 text-sm font-medium ${
              tab === "patients" ? "border-slate-200 bg-white text-slate-900" : "border-transparent text-slate-600"
            }`}
          >
            Grouped by patient phone
          </button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-end gap-4">
        {isSuperAdmin ? (
          <div>
            <label htmlFor="conv-client" className="block text-xs font-medium text-slate-600">Client</label>
            <select
              id="conv-client"
              value={clientId}
              onChange={(event) => {
                setClientId(event.target.value);
                setPage(0);
              }}
              className={`mt-1 ${selectClass}`}
            >
              <option value="">Choose a client</option>
              {clients.map((client) => (
                <option key={client.id} value={client.id}>{client.name}</option>
              ))}
            </select>
          </div>
        ) : null}
        <div>
          <label htmlFor="conv-range" className="block text-xs font-medium text-slate-600">Range</label>
          <select
            id="conv-range"
            value={preset}
            onChange={(event) => {
              setPreset(event.target.value as RangePreset);
              setPage(0);
            }}
            className={`mt-1 ${selectClass}`}
          >
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
          </select>
        </div>
        {tab === "list" ? (
          <div>
            <label htmlFor="conv-outcome" className="block text-xs font-medium text-slate-600">Outcome</label>
            <select
              id="conv-outcome"
              value={outcome}
              onChange={(event) => {
                setOutcome(event.target.value as OutcomeKey | "in_progress" | "");
                setPage(0);
              }}
              className={`mt-1 ${selectClass}`}
            >
              {OUTCOME_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        ) : null}
        {tab === "list" && canSeePatients ? (
          <form onSubmit={applyPhone} className="flex items-end gap-2">
            <div>
              <label htmlFor="conv-phone" className="block text-xs font-medium text-slate-600">
                Find by phone (exact number)
              </label>
              <input
                id="conv-phone"
                type="tel"
                autoComplete="off"
                value={phoneDraft}
                onChange={(event) => setPhoneDraft(event.target.value)}
                placeholder="312-555-0187"
                className={`mt-1 w-48 ${selectClass}`}
              />
            </div>
            <button type="submit" className={buttonClass}>Find</button>
            {phone ? (
              <button
                type="button"
                className={buttonClass}
                onClick={() => {
                  setPhone("");
                  setPhoneDraft("");
                  setPage(0);
                }}
              >
                Clear
              </button>
            ) : null}
          </form>
        ) : null}
      </div>

      {isSuperAdmin && !clientId ? (
        <p className="text-sm text-slate-600">Choose a client to see its conversations.</p>
      ) : null}

      {error ? (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-900">{error}</p>
      ) : null}

      {!(isSuperAdmin && !clientId) && tab === "list" ? (
        <section id="panel-list" role="tabpanel" aria-labelledby="tab-list" className="space-y-3">
          {phone ? (
            <p className="text-sm text-slate-700">
              Showing conversations for this phone number. {total} found.
            </p>
          ) : null}
          {items === null ? (
            <p className="text-sm text-slate-500">Loading conversations...</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-slate-600">No conversations match these filters.</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Conversations in the selected period</caption>
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-2">Started</th>
                    <th scope="col" className="px-4 py-2">Outcome</th>
                    <th scope="col" className="px-4 py-2">Reason</th>
                    <th scope="col" className="px-4 py-2">Messages</th>
                    <th scope="col" className="px-4 py-2">Unanswered</th>
                    <th scope="col" className="px-4 py-2">
                      <span className="sr-only">Transcript</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {items.map((item) => (
                    <tr key={item.session_id}>
                      <td className="px-4 py-2 text-slate-900">{new Date(item.started_at).toLocaleString()}</td>
                      <td className="px-4 py-2 text-slate-800">{statusText(item)}</td>
                      <td className="max-w-xs px-4 py-2 text-slate-600">{item.reason ?? "-"}</td>
                      <td className="px-4 py-2 tabular-nums">{item.visitor_messages ?? "-"}</td>
                      <td className="px-4 py-2 tabular-nums">{item.unanswered_questions ?? "-"}</td>
                      <td className="px-4 py-2 text-right">
                        {canSeePatients ? (
                          <button
                            type="button"
                            className={buttonClass}
                            onClick={() => setOpenSession(item.session_id)}
                          >
                            View transcript
                            <span className="sr-only"> from {new Date(item.started_at).toLocaleString()}</span>
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      {!(isSuperAdmin && !clientId) && tab === "patients" && canSeePatients ? (
        <section id="panel-patients" role="tabpanel" aria-labelledby="tab-patients" className="space-y-3">
          <p className="text-xs text-slate-600">
            Patients who shared a phone number in chat, grouped by that number. Only the last four
            digits are shown. To see one patient&apos;s conversations, use Find by phone on the
            list. Each row opens that patient&apos;s most recent conversation.
          </p>
          {groups === null ? (
            <p className="text-sm text-slate-500">Loading patients...</p>
          ) : groups.length === 0 ? (
            <p className="text-sm text-slate-600">No patients shared a phone number in this period.</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Patients grouped by phone number</caption>
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="px-4 py-2">Name</th>
                    <th scope="col" className="px-4 py-2">Phone</th>
                    <th scope="col" className="px-4 py-2">Conversations</th>
                    <th scope="col" className="px-4 py-2">First seen</th>
                    <th scope="col" className="px-4 py-2">Last seen</th>
                    <th scope="col" className="px-4 py-2">
                      <span className="sr-only">Open</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {groups.map((group) => (
                    <tr key={group.patient_key}>
                      <td className="px-4 py-2 text-slate-900">{group.name ?? "Not recorded"}</td>
                      <td className="px-4 py-2 font-mono text-xs text-slate-700">{group.masked_phone ?? "Not available"}</td>
                      <td className="px-4 py-2 tabular-nums">{group.conversations}</td>
                      <td className="px-4 py-2 text-slate-600">{new Date(group.first_seen_at).toLocaleDateString()}</td>
                      <td className="px-4 py-2 text-slate-600">{new Date(group.last_seen_at).toLocaleDateString()}</td>
                      <td className="px-4 py-2 text-right">
                        {group.latest_session_id ? (
                          <button
                            type="button"
                            className={buttonClass}
                            onClick={() => setOpenSession(group.latest_session_id)}
                          >
                            Open latest
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      {count > PAGE_SIZE ? (
        <nav aria-label="Pages" className="flex items-center gap-3 text-sm">
          <button type="button" className={buttonClass} disabled={page === 0}
            onClick={() => setPage((current) => Math.max(0, current - 1))}>
            Previous
          </button>
          <span className="text-slate-600">Page {page + 1} of {lastPage + 1}</span>
          <button type="button" className={buttonClass} disabled={page >= lastPage}
            onClick={() => setPage((current) => Math.min(lastPage, current + 1))}>
            Next
          </button>
        </nav>
      ) : null}

      {openSession ? (
        <ConversationDrawer
          sessionId={openSession}
          clientId={clientId || undefined}
          onClose={() => setOpenSession(null)}
        />
      ) : null}
    </div>
  );
}
