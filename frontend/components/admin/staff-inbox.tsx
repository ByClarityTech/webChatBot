"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import {
  acceptChat,
  CallbackItem,
  Conversation,
  conversation as loadConversation,
  endChat,
  handleCallback,
  heartbeat,
  InboxQueues,
  openStaffStream,
  queues as loadQueues,
  requestDraft,
  saveDraftFaq,
  sendReply,
} from "@/lib/inbox-api";
import { canAccess } from "@/lib/admin-permissions";
import { consumeSse } from "@/lib/sse";

const HEARTBEAT_MS = 20_000;
const REQUEST_WINDOW_SECONDS = 180;
const EVENTS_THAT_CHANGE_QUEUES = [
  "handover_requested",
  "handover_accepted",
  "handover_timeout",
  "handover_ended",
  "visitor_message",
  "agent_message",
];

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function mmss(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}

function tone(context: AudioContext) {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.frequency.value = 880;
  gain.gain.value = 0.2;
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start();
  oscillator.stop(context.currentTime + 0.25);
}

/**
 * Alert sound needs a click first (browsers block autoplay). The click creates and resumes
 * the AudioContext, then plays a confirmation tone so the user hears that it worked.
 * Sound is only marked on once the context is actually running.
 */
function useAlertSound() {
  const contextRef = useRef<AudioContext | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [failed, setFailed] = useState(false);

  const enable = useCallback(async () => {
    setFailed(false);
    try {
      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) {
        setFailed(true);
        return;
      }
      const context = contextRef.current ?? new Ctor();
      contextRef.current = context;
      await context.resume();
      if (context.state !== "running") {
        setFailed(true);
        return;
      }
      tone(context);
      setEnabled(true);
    } catch {
      setFailed(true);
    }
  }, []);

  const play = useCallback(() => {
    const context = contextRef.current;
    if (!context || context.state !== "running") {
      return;
    }
    tone(context);
  }, []);

  return { enabled, failed, enable, play };
}

export function StaffInbox() {
  const actor = useAdminUser();
  const canSaveFaq = canAccess(actor.user_type, "documents");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;
  const sound = useAlertSound();
  const tabId = useRef<string>(crypto.randomUUID());

  const [queues, setQueues] = useState<InboxQueues | null>(null);
  const [deadlines, setDeadlines] = useState<Record<string, number>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chat, setChat] = useState<Conversation | null>(null);
  const [reply, setReply] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [faqAnswer, setFaqAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [streamUp, setStreamUp] = useState(false);
  const searchParams = useSearchParams();
  const linkedSession = searchParams.get("conversation");
  const linkedRef = useRef<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  const refreshQueues = useCallback(async () => {
    if (needsClient) return;
    try {
      const data = await loadQueues(withClient);
      setQueues(data);
      const nextDeadlines: Record<string, number> = {};
      for (const item of data.pending) {
        nextDeadlines[item.id] = Date.now() + item.seconds_left * 1000;
      }
      setDeadlines((current) => ({ ...current, ...nextDeadlines }));
    } catch (err) {
      setError(errorText(err, "Could not load the inbox."));
    }
    // scope.scope is the only input that changes the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsClient, scope.scope]);

  const refreshChat = useCallback(async (id: string) => {
    try {
      setChat(await loadConversation(withClient, id));
    } catch (err) {
      setError(errorText(err, "Could not open this conversation."));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.scope]);

  // Queues on load and whenever the client changes.
  useEffect(() => {
    void refreshQueues();
  }, [refreshQueues]);

  // Live events, with a reconnect loop. A reconnect re-reads the queues: nothing is replayed.
  const latest = useRef({ refreshQueues, refreshChat, selectedId, play: sound.play });
  latest.current = { refreshQueues, refreshChat, selectedId, play: sound.play };

  useEffect(() => {
    if (needsClient) return;
    const controller = new AbortController();
    let retry = 0;

    async function loop() {
      while (!controller.signal.aborted) {
        try {
          const response = await openStaffStream(withClient, controller.signal);
          setStreamUp(true);
          retry = 0;
          await consumeSse(
            response,
            (event, data) => {
              const current = latest.current;
              if (event === "handover_requested") {
                current.play();
              }
              if (EVENTS_THAT_CHANGE_QUEUES.includes(event)) {
                void current.refreshQueues();
              }
              const payload = data as { handover_id?: string };
              if (current.selectedId && payload?.handover_id === current.selectedId) {
                void current.refreshChat(current.selectedId);
              }
            },
            controller.signal,
          );
        } catch {
          // fall through to reconnect
        }
        setStreamUp(false);
        if (controller.signal.aborted) return;
        retry = Math.min(retry + 1, 4);
        await new Promise((resolve) => setTimeout(resolve, retry * 2000));
        void latest.current.refreshQueues();
      }
    }

    void loop();
    return () => controller.abort();
    // The stream follows the client scope only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsClient, scope.scope]);

  // Presence: each open tab beats, so staff online counts stay right across tabs.
  useEffect(() => {
    if (needsClient) return;
    let cancelled = false;
    async function beat() {
      try {
        const result = await heartbeat(withClient, tabId.current);
        if (!cancelled) {
          setQueues((current) => (current ? { ...current, online: result.online } : current));
        }
      } catch {
        // The next beat tries again.
      }
    }
    void beat();
    const timer = setInterval(beat, HEARTBEAT_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsClient, scope.scope]);

  // Countdowns redraw every second.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // A "View chat" link from the Callbacks queue opens that live conversation.
  useEffect(() => {
    if (!linkedSession || !queues || linkedRef.current === linkedSession) return;
    const match = queues.active.find((item) => item.session_id === linkedSession);
    if (match) {
      linkedRef.current = linkedSession;
      setSelectedId(match.id);
    }
  }, [linkedSession, queues]);

  // The tab title carries the pending count.
  useEffect(() => {
    const pending = queues?.pending.length ?? 0;
    document.title = pending > 0 ? `(${pending}) Staff inbox` : "Staff inbox";
  }, [queues]);

  useEffect(() => {
    if (selectedId) {
      setDraft(null);
      setNotice(null);
      void refreshChat(selectedId);
    } else {
      setChat(null);
    }
  }, [selectedId, refreshChat]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err, "That did not work. Try again."));
    } finally {
      setBusy(false);
    }
  }

  function accept(id: string) {
    void run(async () => {
      await acceptChat(withClient, id);
      setSelectedId(id);
      await refreshQueues();
    });
  }

  function send(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !reply.trim()) return;
    void run(async () => {
      await sendReply(withClient, selectedId, reply.trim());
      setReply("");
      setDraft(null);
      await refreshChat(selectedId);
    });
  }

  function askForDraft() {
    if (!selectedId) return;
    void run(async () => {
      const result = await requestDraft(withClient, selectedId);
      setDraft(result.draft);
    });
  }

  function finish() {
    if (!selectedId || !window.confirm("End this chat? The visitor will be told it has ended.")) return;
    void run(async () => {
      await endChat(withClient, selectedId);
      setSelectedId(null);
      await refreshQueues();
    });
  }

  function saveAnswerAsFaq(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !faqAnswer.trim()) return;
    void run(async () => {
      await saveDraftFaq(withClient, selectedId, faqAnswer.trim());
      setFaqAnswer("");
      setNotice("Saved as a draft FAQ. An admin reviews and approves it before the chatbot uses it.");
    });
  }

  function markCallbackHandled(item: CallbackItem) {
    void run(async () => {
      await handleCallback(withClient, item.id);
      await refreshQueues();
    });
  }

  // Keep the newest message in view when the thread changes or a message arrives.
  useEffect(() => {
    const el = messagesRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [chat?.id, chat?.messages.length]);

  const iAccepted = chat !== null && chat.accepted_by === actor.id;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-3">
        <div className="flex items-center gap-3 text-sm text-slate-700">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${streamUp ? "bg-green-500" : "bg-slate-300"}`}
            aria-hidden
          />
          <span role="status">
            {queues ? `${queues.online} staff online` : "Connecting..."}
            {!streamUp && " · reconnecting"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <ClientPicker scope={scope} id="inbox-client" />
          {sound.enabled ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-green-50 px-3 py-1.5 text-sm text-green-800">
              <span className="inline-block h-2 w-2 rounded-full bg-green-500" aria-hidden />
              Alert sound on
            </span>
          ) : (
            <button
              type="button"
              onClick={() => void sound.enable()}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
            >
              Enable alert sound
            </button>
          )}
        </div>
      </div>

      {!sound.enabled && (
        <p className="shrink-0 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          {sound.failed
            ? "Sound could not be turned on in this browser. Check that sound is allowed for this site, then try again."
            : "Sound alerts are off. Browsers block sound until you click. Enable alert sound to hear new chats."}
        </p>
      )}
      {error && (
        <p className="shrink-0 text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="shrink-0 text-sm text-green-700" role="status">
          {notice}
        </p>
      )}

      {needsClient ? (
        <p className="shrink-0 text-sm text-slate-500">Choose a client to see its inbox.</p>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden lg:flex-row">
          <aside className="max-h-64 shrink-0 space-y-5 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-4 lg:h-full lg:max-h-none lg:w-80">
            <section>
              <h2 className="text-sm font-semibold text-slate-900">Pending ({queues?.pending.length ?? 0})</h2>
              <ul className="mt-2 space-y-2">
                {(queues?.pending ?? []).map((item) => {
                  const left = Math.max(0, Math.ceil(((deadlines[item.id] ?? 0) - now) / 1000));
                  return (
                    <li key={item.id} className="rounded-lg border border-slate-200 p-3 text-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-900">
                          {item.contact?.name ?? "Anonymous visitor"}
                        </span>
                        <span className={`font-mono text-xs ${left <= 30 ? "text-red-600" : "text-slate-600"}`}>
                          {mmss(left || REQUEST_WINDOW_SECONDS)}
                        </span>
                      </div>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => accept(item.id)}
                        className="mt-2 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                      >
                        Accept
                      </button>
                    </li>
                  );
                })}
                {(queues?.pending.length ?? 0) === 0 && (
                  <li className="text-xs text-slate-500">No one is waiting.</li>
                )}
              </ul>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-900">Active ({queues?.active.length ?? 0})</h2>
              <ul className="mt-2 space-y-2">
                {(queues?.active ?? []).map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      className={`w-full rounded-lg border p-2 text-left text-sm ${
                        selectedId === item.id ? "border-slate-900" : "border-slate-200"
                      }`}
                    >
                      Chat started {formatTime(item.accepted_at ?? "")}
                    </button>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-900">Callbacks ({queues?.callbacks.length ?? 0})</h2>
              <ul className="mt-2 space-y-2">
                {(queues?.callbacks ?? []).map((item) => (
                  <li key={item.id} className="rounded-lg border border-slate-200 p-3 text-sm">
                    <p className="font-medium text-slate-900">{item.contact?.name ?? "Details awaited"}</p>
                    {item.contact?.phone && <p className="text-slate-700">{item.contact.phone}</p>}
                    <p className="mt-1 text-xs text-slate-500">{item.source}</p>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => markCallbackHandled(item)}
                        className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs text-slate-700 disabled:opacity-50"
                      >
                        Mark handled
                      </button>
                      {item.view_chat && (
                        <Link href={item.view_chat} className="text-xs underline">
                          View chat
                        </Link>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </aside>

          <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white p-4">
            {!chat ? (
              <p className="m-auto text-sm text-slate-500">Choose a chat, or accept one from Pending.</p>
            ) : (
              <>
                <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">
                      {chat.contact?.name ?? "Anonymous visitor"}
                      {chat.is_test_session && <span className="ml-2 text-xs text-amber-700">Test</span>}
                    </p>
                    {chat.contact?.phone && <p className="text-xs text-slate-600">{chat.contact.phone}</p>}
                  </div>
                  {iAccepted && (
                    <button
                      type="button"
                      onClick={finish}
                      disabled={busy}
                      className="rounded-lg border border-red-200 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50"
                    >
                      End chat
                    </button>
                  )}
                </header>

                <div ref={messagesRef} className="my-3 min-h-0 flex-1 space-y-2 overflow-y-auto">
                  {chat.messages.map((message) => (
                    <div key={message.id} className={`flex ${message.sender === "staff" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                          message.sender === "staff" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-900"
                        }`}
                      >
                        <p className="mb-0.5 text-[10px] uppercase tracking-wide opacity-70">
                          {message.sender === "staff" ? "Staff" : "Visitor"} · {formatTime(message.created_at)}
                        </p>
                        <p className="whitespace-pre-wrap break-words">{message.body}</p>
                      </div>
                    </div>
                  ))}
                </div>

                {!iAccepted ? (
                  <p className="shrink-0 text-sm text-slate-600">
                    {chat.accepted_by
                      ? "Another team member is handling this chat."
                      : "Accept this chat to reply."}
                  </p>
                ) : (
                  <div className="shrink-0 space-y-3">
                    {draft && (
                      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-3" aria-label="AI draft suggestion">
                        <p className="text-xs font-medium text-slate-600">AI draft (suggestion, not sent)</p>
                        <p className="mt-1 whitespace-pre-wrap text-sm text-slate-900">{draft}</p>
                        <div className="mt-2 flex gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              setReply(draft);
                              setDraft(null);
                            }}
                            className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs text-white"
                          >
                            Use draft
                          </button>
                          <button
                            type="button"
                            onClick={() => setDraft(null)}
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700"
                          >
                            Discard
                          </button>
                        </div>
                      </div>
                    )}
                    <form onSubmit={send} className="flex gap-2">
                      <label htmlFor="staff-reply" className="sr-only">
                        Reply to the visitor
                      </label>
                      <textarea
                        id="staff-reply"
                        value={reply}
                        onChange={(event) => setReply(event.target.value)}
                        rows={2}
                        maxLength={2000}
                        className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-sm"
                        placeholder="Type a reply..."
                        disabled={busy}
                      />
                      <div className="flex flex-col gap-2">
                        <button
                          type="submit"
                          disabled={busy || !reply.trim()}
                          className="rounded-xl bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
                        >
                          Send
                        </button>
                        <button
                          type="button"
                          onClick={askForDraft}
                          disabled={busy}
                          className="rounded-xl border border-slate-300 px-4 py-2 text-xs text-slate-700 disabled:opacity-50"
                        >
                          Draft with AI
                        </button>
                      </div>
                    </form>

                    {canSaveFaq && (
                      <form onSubmit={saveAnswerAsFaq} className="rounded-xl border border-slate-200 p-3">
                        <label htmlFor="faq-answer" className="text-xs font-medium text-slate-700">
                          Save this answer as a draft FAQ for review
                        </label>
                        <textarea
                          id="faq-answer"
                          value={faqAnswer}
                          onChange={(event) => setFaqAnswer(event.target.value)}
                          rows={2}
                          maxLength={2000}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                        />
                        <button
                          type="submit"
                          disabled={busy || !faqAnswer.trim()}
                          className="mt-2 rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 disabled:opacity-50"
                        >
                          Save as draft FAQ
                        </button>
                      </form>
                    )}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
