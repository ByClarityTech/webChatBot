"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import {
  ApiError,
  Faq,
  FaqList,
  ManagedClient,
  approveAllFaqs,
  createFaq,
  deleteFaq,
  generateFaqs,
  listClients,
  listDocuments,
  listFaqs,
  setDefaultFaqs,
  updateFaq,
} from "@/lib/admin-api";

// Mirror backend/app/schemas/faqs.py; the API checks again.
const QUESTION_MAX_LENGTH = 200;
const ANSWER_MAX_LENGTH = 2000;

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

function sourceLabel(faq: Faq): string {
  if (faq.source_removed) {
    return `Source removed (${faq.source_filename})`;
  }
  return faq.source_filename ? `From ${faq.source_filename}` : "Added by hand";
}

const inputClass = "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const buttonClass =
  "rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50";

export function FaqManager() {
  const actor = useAdminUser();
  const isSuperAdmin = actor.user_type === "super_admin";

  // Super Admin only: which client's FAQs to show. Admins always see their own.
  const [clients, setClients] = useState<ManagedClient[]>([]);
  const [clientId, setClientId] = useState("");
  const needsClient = isSuperAdmin && !clientId;
  const scope = isSuperAdmin ? clientId : undefined;

  const [data, setData] = useState<FaqList | null>(null);
  const [readyDocuments, setReadyDocuments] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const [newQuestion, setNewQuestion] = useState("");
  const [newAnswer, setNewAnswer] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editQuestion, setEditQuestion] = useState("");
  const [editAnswer, setEditAnswer] = useState("");

  const load = useCallback(async () => {
    if (needsClient) {
      setData(null);
      setReadyDocuments(0);
      return;
    }
    const [faqs, documents] = await Promise.all([listFaqs(scope), listDocuments(scope)]);
    setData(faqs);
    setReadyDocuments(documents.documents.filter((doc) => doc.status === "ready").length);
  }, [needsClient, scope]);

  useEffect(() => {
    if (!isSuperAdmin) {
      return;
    }
    listClients()
      .then(setClients)
      .catch((err) => setError(errorMessage(err, "Failed to load clients.")));
  }, [isSuperAdmin]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    load()
      .catch((err) => {
        if (!cancelled) {
          setError(errorMessage(err, "Failed to load FAQs."));
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
  }, [load]);

  /** Runs one change, shows its result or error, then reloads the list. */
  async function run(key: string, action: () => Promise<string | null>, fallback: string) {
    setBusy(key);
    setError(null);
    setSuccess(null);
    try {
      setSuccess(await action());
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setBusy(null);
      await load().catch(() => undefined);
    }
  }

  const faqs = data?.faqs ?? [];
  const maxDefaults = data?.max_defaults ?? 4;
  const defaults = faqs
    .filter((faq) => faq.is_default)
    .sort((a, b) => (a.default_order ?? 0) - (b.default_order ?? 0));
  const draftCount = faqs.filter((faq) => faq.status === "draft").length;
  const term = search.trim().toLowerCase();
  const shown = term ? faqs.filter((faq) => faq.question.toLowerCase().includes(term)) : faqs;

  function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = newQuestion.trim();
    const answer = newAnswer.trim();
    if (!question || !answer) {
      setError("Enter a question and an answer.");
      return;
    }
    void run(
      "add",
      async () => {
        await createFaq(question, answer, scope);
        setNewQuestion("");
        setNewAnswer("");
        return "Added as a draft. Approve it to use it in the chatbot.";
      },
      "Add failed.",
    );
  }

  function handleGenerate() {
    void run(
      "generate",
      async () => {
        const result = await generateFaqs(scope);
        const parts = [
          `Created ${result.created.length} draft${result.created.length === 1 ? "" : "s"} from ${result.documents_used.join(", ")}.`,
        ];
        if (result.skipped_existing > 0) {
          parts.push(`${result.skipped_existing} suggested question(s) you already have were skipped.`);
        }
        if (result.truncated || result.documents_skipped.length > 0) {
          parts.push(
            "Some document text was over the limit for one run" +
              (result.documents_skipped.length > 0
                ? ` (not read: ${result.documents_skipped.join(", ")})`
                : "") +
              "; run Generate again to cover more.",
          );
        }
        return parts.join(" ");
      },
      "Generation failed.",
    );
  }

  function handleApproveAll() {
    void run(
      "approve-all",
      async () => {
        const approved = await approveAllFaqs(scope);
        return `Approved ${approved} FAQ${approved === 1 ? "" : "s"}.`;
      },
      "Approve failed.",
    );
  }

  function handleStatus(faq: Faq, status: "approved" | "draft") {
    void run(
      faq.id,
      async () => {
        await updateFaq(faq.id, { status }, scope);
        return status === "approved"
          ? "Approved. The chatbot can use it now."
          : "Moved back to draft. The chatbot no longer uses it.";
      },
      "Update failed.",
    );
  }

  function handleSaveEdit(faq: Faq) {
    const question = editQuestion.trim();
    const answer = editAnswer.trim();
    if (!question || !answer) {
      setError("Enter a question and an answer.");
      return;
    }
    const changes = {
      ...(question !== faq.question ? { question } : {}),
      ...(answer !== faq.answer ? { answer } : {}),
    };
    if (Object.keys(changes).length === 0) {
      setEditingId(null);
      return;
    }
    void run(
      faq.id,
      async () => {
        await updateFaq(faq.id, changes, scope);
        setEditingId(null);
        return faq.status === "approved" ? "Saved. The chatbot uses the new text." : "Saved.";
      },
      "Save failed.",
    );
  }

  function handleDelete(faq: Faq) {
    if (!window.confirm(`Delete the FAQ "${faq.question}"?`)) {
      return;
    }
    void run(
      faq.id,
      async () => {
        await deleteFaq(faq.id, scope);
        return "Deleted.";
      },
      "Delete failed.",
    );
  }

  function saveDefaults(ids: string[], message: string) {
    void run(
      "defaults",
      async () => {
        await setDefaultFaqs(ids, scope);
        return message;
      },
      "Could not change the default questions.",
    );
  }

  function toggleDefault(faq: Faq) {
    const ids = defaults.map((item) => item.id);
    if (faq.is_default) {
      saveDefaults(
        ids.filter((id) => id !== faq.id),
        "Removed from the default questions.",
      );
    } else {
      saveDefaults([...ids, faq.id], "Added to the default questions.");
    }
  }

  function moveDefault(index: number, offset: -1 | 1) {
    const ids = defaults.map((item) => item.id);
    const target = index + offset;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    saveDefaults(ids, "Order saved.");
  }

  const disabled = busy !== null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        {isSuperAdmin && (
          <div>
            <label htmlFor="faqs-client" className="block text-xs font-medium text-slate-600">
              Client
            </label>
            <select
              id="faqs-client"
              value={clientId}
              onChange={(event) => {
                setClientId(event.target.value);
                setSuccess(null);
                setEditingId(null);
              }}
              className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            >
              <option value="">Choose a client</option>
              {clients.map((client) => (
                <option key={client.id} value={client.id}>
                  {client.name}
                  {client.status === "suspended" ? " (suspended)" : ""}
                </option>
              ))}
            </select>
          </div>
        )}
        {!needsClient && data && (
          <p className="text-sm text-slate-600">
            {faqs.length} FAQ{faqs.length === 1 ? "" : "s"} · {draftCount} draft
            {draftCount === 1 ? "" : "s"} · {defaults.length} of {maxDefaults} defaults
          </p>
        )}
      </div>

      {needsClient ? (
        <p className="rounded-2xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
          Choose a client to see and edit its FAQs.
        </p>
      ) : loading && !data ? (
        <p className="text-sm text-slate-500">Loading FAQs...</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={disabled || readyDocuments === 0}
              onClick={handleGenerate}
              className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy === "generate" ? "Generating... (this takes a few seconds)" : "Generate FAQs"}
            </button>
            {draftCount > 0 && (
              <button
                type="button"
                disabled={disabled}
                onClick={handleApproveAll}
                className={buttonClass}
              >
                {busy === "approve-all" ? "Approving..." : `Approve all ${draftCount} drafts`}
              </button>
            )}
            <p className="text-xs text-slate-500">
              {readyDocuments === 0
                ? "Upload a document on the Documents page to generate FAQs from it."
                : "Generated FAQs are drafts. Check them before approving: only approved FAQs reach the chatbot."}
            </p>
          </div>

          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
          {success && <p className="text-sm text-green-700">{success}</p>}

          <section className="rounded-2xl border border-slate-200 bg-white p-6">
            <h2 className="text-lg font-semibold text-slate-900">Default questions</h2>
            <p className="mt-1 text-sm text-slate-600">
              Shown as buttons when a visitor opens the chat. Star up to {maxDefaults} approved
              FAQs below.
            </p>
            {defaults.length === 0 ? (
              <p className="mt-4 text-sm text-slate-500">No default questions yet.</p>
            ) : (
              <div className="mt-4 grid gap-6 lg:grid-cols-2">
                <ol className="space-y-2">
                  {defaults.map((faq, index) => (
                    <li
                      key={faq.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    >
                      <span className="text-slate-900">
                        {index + 1}. {faq.question}
                      </span>
                      <span className="flex shrink-0 gap-1">
                        <button
                          type="button"
                          aria-label={`Move "${faq.question}" up`}
                          disabled={disabled || index === 0}
                          onClick={() => moveDefault(index, -1)}
                          className={buttonClass}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`Move "${faq.question}" down`}
                          disabled={disabled || index === defaults.length - 1}
                          onClick={() => moveDefault(index, 1)}
                          className={buttonClass}
                        >
                          ↓
                        </button>
                      </span>
                    </li>
                  ))}
                </ol>
                <div aria-label="Chat preview" className="rounded-xl bg-slate-50 p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    Chat preview
                  </p>
                  <div className="mt-3 flex flex-col gap-2">
                    {defaults.map((faq) => (
                      <span
                        key={faq.id}
                        className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-700"
                      >
                        {faq.question}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </section>

          <form onSubmit={handleAdd} className="rounded-2xl border border-slate-200 bg-white p-6" noValidate>
            <h2 className="text-lg font-semibold text-slate-900">Add FAQ</h2>
            <div className="mt-4 grid gap-3">
              <input
                aria-label="New question"
                placeholder="Question a visitor might ask"
                value={newQuestion}
                maxLength={QUESTION_MAX_LENGTH}
                onChange={(event) => setNewQuestion(event.target.value)}
                className={inputClass}
                disabled={disabled}
              />
              <textarea
                aria-label="New answer"
                placeholder="Answer (plain text)"
                value={newAnswer}
                maxLength={ANSWER_MAX_LENGTH}
                onChange={(event) => setNewAnswer(event.target.value)}
                rows={3}
                className={inputClass}
                disabled={disabled}
              />
              <div>
                <button type="submit" disabled={disabled} className={buttonClass}>
                  {busy === "add" ? "Adding..." : "Add as draft"}
                </button>
              </div>
            </div>
          </form>

          <div className="space-y-3">
            <input
              aria-label="Search FAQs"
              placeholder="Search questions"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className={`${inputClass} max-w-sm`}
            />
            {faqs.length === 0 ? (
              <p className="rounded-2xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-500">
                No FAQs yet. Generate them from your documents or add one above.
              </p>
            ) : shown.length === 0 ? (
              <p className="text-sm text-slate-500">No questions match your search.</p>
            ) : (
              <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
                {shown.map((faq) => {
                  const approved = faq.status === "approved";
                  const editing = editingId === faq.id;
                  const starLocked = !faq.is_default && (!approved || defaults.length >= maxDefaults);
                  return (
                    <li key={faq.id} className="px-4 py-4">
                      {editing ? (
                        <div className="grid gap-2">
                          <input
                            aria-label="Edit question"
                            value={editQuestion}
                            maxLength={QUESTION_MAX_LENGTH}
                            onChange={(event) => setEditQuestion(event.target.value)}
                            className={inputClass}
                            disabled={disabled}
                          />
                          <textarea
                            aria-label="Edit answer"
                            value={editAnswer}
                            maxLength={ANSWER_MAX_LENGTH}
                            onChange={(event) => setEditAnswer(event.target.value)}
                            rows={3}
                            className={inputClass}
                            disabled={disabled}
                          />
                          <div className="flex gap-2">
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => handleSaveEdit(faq)}
                              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                            >
                              {busy === faq.id ? "Saving..." : "Save"}
                            </button>
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => setEditingId(null)}
                              className={buttonClass}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <p className="font-medium text-slate-900">{faq.question}</p>
                            <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">
                              {faq.answer}
                            </p>
                            <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                              <span
                                className={`inline-flex rounded-full px-2 py-0.5 font-medium ${
                                  approved ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                                }`}
                              >
                                {approved ? "Approved" : "Draft"}
                              </span>
                              <span className={faq.source_removed ? "text-red-700" : undefined}>
                                {sourceLabel(faq)}
                              </span>
                            </p>
                          </div>
                          <div className="flex shrink-0 flex-wrap gap-2">
                            <button
                              type="button"
                              aria-label={
                                faq.is_default ? "Remove from default questions" : "Make a default question"
                              }
                              aria-pressed={faq.is_default}
                              title={
                                !approved
                                  ? "Approve it first"
                                  : starLocked
                                    ? `Up to ${maxDefaults} default questions`
                                    : undefined
                              }
                              disabled={disabled || starLocked}
                              onClick={() => toggleDefault(faq)}
                              className={`${buttonClass} ${faq.is_default ? "border-amber-300 bg-amber-50 text-amber-800" : ""}`}
                            >
                              {faq.is_default ? "★ Default" : "☆ Default"}
                            </button>
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => handleStatus(faq, approved ? "draft" : "approved")}
                              className={buttonClass}
                            >
                              {busy === faq.id ? "Working..." : approved ? "Unapprove" : "Approve"}
                            </button>
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => {
                                setEditingId(faq.id);
                                setEditQuestion(faq.question);
                                setEditAnswer(faq.answer);
                              }}
                              className={buttonClass}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => handleDelete(faq)}
                              className="rounded-lg border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
