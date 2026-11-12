"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  ChatUnavailableError,
  clientSlugFromUrl,
  closeSession,
  ConsentOffer,
  getConsentOffer,
  getSessionStatus,
  getWidgetConfig,
  sendMessage,
  startSession,
  submitShareConsent,
} from "@/lib/api";
import { chatConfig, MAX_SUGGESTED_QUESTIONS } from "@/lib/chat-config";

import { BookingForm } from "./booking-form";
import { ShareDetailsForm, ShareValues } from "./share-details-form";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type Phase = "loading" | "choice" | "share" | "chat" | "unavailable";

// Only identifiers are kept in sessionStorage. Messages stay in memory, so a transcript is not
// left on a shared device (PRD privacy). Keys are per client, so two widgets never share one.
function threadStorageKey(client: string): string {
  return `website-assistant-thread-id:${client}`;
}

function sessionStorageKey(client: string): string {
  return `website-assistant-session-id:${client}`;
}

// Earlier builds stored transcripts here. Remove any that are left behind.
function legacyMessagesKey(client: string): string {
  return `website-assistant-messages:${client}`;
}

const IDLE_NOTICE = "Your last conversation ended after 30 minutes without activity. Start a new one to continue.";
const CLOSED_NOTICE = "That conversation has ended. Start a new one to continue.";

// Accepts whatever the config endpoint sent. Anything that is not a non-empty string is
// dropped, duplicates are removed, and the list is capped at the chip limit.
function cleanSuggestedQuestions(raw: unknown): string[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const seen = new Set<string>();
  const questions: string[] = [];
  for (const item of raw) {
    if (typeof item !== "string") {
      continue;
    }
    const trimmed = item.trim();
    if (!trimmed || seen.has(trimmed)) {
      continue;
    }
    seen.add(trimmed);
    questions.push(trimmed);
    if (questions.length === MAX_SUGGESTED_QUESTIONS) {
      break;
    }
  }
  return questions;
}

type ChatPanelProps = {
  /** @deprecated Use `embed` instead */
  compact?: boolean;
  embed?: boolean;
};

export function ChatPanel({ compact = false, embed = false }: ChatPanelProps) {
  const isEmbed = embed || compact;

  const [phase, setPhase] = useState<Phase>("loading");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // Known = the visitor shared name and phone with consent in this conversation.
  const [known, setKnown] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [showBookingForm, setShowBookingForm] = useState(false);
  const [shareSubmitting, setShareSubmitting] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  // The client whose assistant this is, from the page URL (?client=...).
  const [clientSlug, setClientSlug] = useState<string | null>(null);
  // The client's display name, from the widget config; shown in the header.
  const [clientName, setClientName] = useState<string | null>(null);
  // The practice's starred default questions (PRD FR-07), from the widget config.
  const [practiceQuestions, setPracticeQuestions] = useState<string[]>([]);
  // True once the widget config request has finished, either way.
  const [configChecked, setConfigChecked] = useState(false);
  // The approved consent wording. Null means no sharing is offered (fail closed).
  const [offer, setOffer] = useState<ConsentOffer | null>(null);

  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  // Read by the pagehide listener, which must not see stale state.
  const closeRef = useRef<{ slug: string | null; thread: string | null }>({
    slug: null,
    thread: null,
  });

  useEffect(() => {
    closeRef.current = { slug: clientSlug, thread: threadId };
  }, [clientSlug, threadId]);

  // Mount: find the client, restore the conversation's identifiers if still open, load config.
  useEffect(() => {
    const slug = clientSlugFromUrl(window.location.search);
    setClientSlug(slug);
    if (!slug) {
      setPhase("unavailable");
      setHydrated(true);
      return;
    }

    sessionStorage.removeItem(legacyMessagesKey(slug));
    const storedThread = sessionStorage.getItem(threadStorageKey(slug));
    const storedSession = sessionStorage.getItem(sessionStorageKey(slug));
    if (storedThread) {
      getSessionStatus(slug, storedThread)
        .then((status) => {
          if (status.active) {
            setThreadId(storedThread);
            setSessionId(status.session_id ?? storedSession);
            setKnown(status.known);
            setPhase("chat");
          } else {
            sessionStorage.removeItem(threadStorageKey(slug));
            sessionStorage.removeItem(sessionStorageKey(slug));
            setNotice(CLOSED_NOTICE);
            setPhase("choice");
          }
        })
        .catch((statusError) => {
          if (statusError instanceof ChatUnavailableError) {
            setPhase("unavailable");
          } else {
            setPhase("choice");
          }
        });
    } else {
      setPhase("choice");
    }

    getWidgetConfig(slug)
      .then((config) => {
        setClientName(config.name);
        setPracticeQuestions(cleanSuggestedQuestions(config.default_questions));
        setConfigChecked(true);
      })
      .catch((configError) => {
        if (configError instanceof ChatUnavailableError) {
          setPhase("unavailable");
        }
        setConfigChecked(true);
      });

    getConsentOffer(slug)
      .then(setOffer)
      .catch(() => setOffer(null));

    setHydrated(true);
  }, []);

  // Keep only the identifiers in sessionStorage, never the messages. Only writes here: clearing
  // happens where a conversation ends, so the stored ones survive the first render after a reload.
  useEffect(() => {
    if (!hydrated || !clientSlug) {
      return;
    }
    if (threadId) {
      sessionStorage.setItem(threadStorageKey(clientSlug), threadId);
    }
    if (sessionId) {
      sessionStorage.setItem(sessionStorageKey(clientSlug), sessionId);
    }
  }, [threadId, sessionId, hydrated, clientSlug]);

  function forgetStoredConversation() {
    if (clientSlug) {
      sessionStorage.removeItem(threadStorageKey(clientSlug));
      sessionStorage.removeItem(sessionStorageKey(clientSlug));
    }
  }

  // Closing the widget ends the conversation as 'closed' (best effort; idle sessions end anyway).
  useEffect(() => {
    function onPageHide() {
      const { slug, thread } = closeRef.current;
      if (slug && thread) {
        closeSession(slug, thread);
      }
    }
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, []);

  useEffect(() => {
    const container = messagesContainerRef.current;
    const hasNewMessages = messages.length > prevMessageCountRef.current;

    if (container && hasNewMessages) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }

    prevMessageCountRef.current = messages.length;
  }, [messages, loading, showBookingForm]);

  /** Opens a new conversation on the server and clears what was on screen. */
  async function startConversation(): Promise<{ thread: string; session: string } | null> {
    if (!clientSlug) {
      return null;
    }
    setError(null);
    setNotice(null);
    setMessages([]);
    setShowBookingForm(false);
    try {
      const started = await startSession(clientSlug);
      setThreadId(started.thread_id);
      setSessionId(started.session_id);
      setKnown(false);
      return { thread: started.thread_id, session: started.session_id };
    } catch (startError) {
      if (startError instanceof ChatUnavailableError) {
        setPhase("unavailable");
      } else {
        setError(startError instanceof Error ? startError.message : "Could not start a conversation.");
      }
      return null;
    }
  }

  /** Back to the welcome choice, with a reason shown above it. */
  function returnToChoice(reason: string) {
    forgetStoredConversation();
    setMessages([]);
    setThreadId(null);
    setSessionId(null);
    setKnown(false);
    setShowBookingForm(false);
    setNotice(reason);
    setShareError(null);
    setPhase("choice");
  }

  async function chooseJustChat() {
    if (await startConversation()) {
      setPhase("chat");
    }
  }

  async function chooseShare() {
    setShareError(null);
    if (await startConversation()) {
      setPhase("share");
    }
  }

  async function handleShareSubmit(values: ShareValues) {
    if (!offer || !clientSlug || !threadId || !sessionId) {
      return;
    }
    setShareSubmitting(true);
    setShareError(null);
    try {
      await submitShareConsent({
        client: clientSlug,
        thread_id: threadId,
        session_id: sessionId,
        text_version: offer.version,
        full_name: values.full_name,
        phone: values.phone,
        is_current_patient: values.is_current_patient,
        consent_accepted: true,
      });
      setKnown(true);
      setPhase("chat");
    } catch (submitError) {
      if (submitError instanceof ApiRequestError && submitError.status === 409) {
        returnToChoice(CLOSED_NOTICE);
      } else if (submitError instanceof ApiRequestError) {
        setShareError(submitError.message);
      } else {
        setShareError("Could not save your details. You can keep chatting without sharing.");
      }
    } finally {
      setShareSubmitting(false);
    }
  }

  function skipShare() {
    setShareError(null);
    setPhase("chat");
  }

  function handleBookingSuccess(message: string) {
    setShowBookingForm(false);
    // A booking with consent also makes the conversation known from here on.
    if (!known) {
      setKnown(true);
    }
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "assistant", content: message },
    ]);
  }

  async function sendUserMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading || !clientSlug) {
      return;
    }

    setError(null);
    setNotice(null);

    // An idle conversation ends on the server at 30 minutes. Check first, so the visitor is
    // offered a new conversation before anything is sent, and their text stays in the box.
    if (threadId) {
      try {
        const status = await getSessionStatus(clientSlug, threadId);
        if (!status.active) {
          setInput(trimmed);
          returnToChoice(IDLE_NOTICE);
          return;
        }
      } catch {
        // Could not check: send anyway, and the server still applies the rule.
      }
    }

    let activeThread = threadId;
    if (!activeThread) {
      const started = await startConversation();
      if (!started) {
        return;
      }
      activeThread = started.thread;
    }

    setLoading(true);
    setInput("");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: trimmed },
    ]);

    try {
      const response = await sendMessage(trimmed, clientSlug, activeThread);
      if (response.session_reset) {
        // The server found the old conversation ended in the meantime (a race with the check).
        returnToChoice(IDLE_NOTICE);
        return;
      }
      setThreadId(response.thread_id);
      if (response.session_id) {
        setSessionId(response.session_id);
      }
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.reply,
        },
      ]);
      if (response.action === "open_booking_form") {
        setShowBookingForm(true);
      }
    } catch (submitError) {
      if (submitError instanceof ChatUnavailableError) {
        setPhase("unavailable");
        return;
      }
      setError(submitError instanceof Error ? submitError.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await sendUserMessage(input);
  }

  const isEmpty = messages.length === 0 && !loading;
  // The practice's own questions when it has starred any; otherwise the dental fallbacks.
  const suggestedQuestions =
    practiceQuestions.length > 0 ? practiceQuestions : chatConfig.fallbackSuggestedQuestions;
  // Full page: a fixed-size card. Embedded: fills the iframe the client's site sets.
  const frameClass = isEmbed
    ? "flex h-full min-h-0 flex-col overflow-hidden bg-white"
    : "mx-auto flex h-[620px] w-full max-w-[580px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

  if (phase === "unavailable") {
    // No or unknown client, or the client is suspended: nothing to chat with.
    return (
      <div className={`${frameClass} items-center justify-center p-6`}>
        <p className="text-center text-sm text-slate-500" role="status">
          This assistant is not available.
        </p>
      </div>
    );
  }

  return (
    <div className={frameClass}>
      <div className="flex shrink-0 items-center gap-3 bg-[#1D4ED8] px-4 py-3">
        <img
          src="/bot-profile.png"
          alt=""
          className="h-11 w-11 shrink-0 rounded-full border border-white bg-white object-cover"
        />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[15px] font-bold leading-tight text-white">
            {clientName ?? ""}
          </span>
          <span className="text-xs leading-tight text-blue-100">AI Assistant</span>
        </div>
      </div>

      {notice && (
        <p className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900" role="status">
          {notice}
        </p>
      )}

      {phase === "choice" && (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 overflow-y-auto p-4">
          {chatConfig.greeting && (
            <p className="text-center text-sm leading-relaxed text-slate-500">
              {chatConfig.greeting}
            </p>
          )}
          <div className="flex w-full max-w-xs flex-col gap-2">
            {offer && (
              <button
                type="button"
                onClick={chooseShare}
                className="rounded-xl bg-slate-900 px-3 py-2.5 text-sm font-medium text-white"
              >
                Share my details
              </button>
            )}
            <button
              type="button"
              onClick={chooseJustChat}
              className="rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm font-medium text-slate-800"
            >
              Just start chatting
            </button>
          </div>
        </div>
      )}

      {phase === "share" && offer && (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <ShareDetailsForm
            consentText={offer.text}
            submitting={shareSubmitting}
            error={shareError}
            onSubmit={handleShareSubmit}
            onSkip={skipShare}
          />
        </div>
      )}

      {phase === "chat" && (
        <>
          <div
            ref={messagesContainerRef}
            className={`min-h-0 flex-1 overscroll-contain overflow-y-auto p-3 ${
              isEmpty ? "flex items-center justify-center px-4" : "space-y-3 px-4 py-3"
            }`}
          >
            {isEmpty && (
              <div className="flex w-full min-w-0 flex-col items-center gap-4">
                {chatConfig.greeting && (
                  <p className="text-center text-sm leading-relaxed text-slate-500">
                    {chatConfig.greeting}
                  </p>
                )}
                {configChecked && suggestedQuestions.length > 0 && (
                  <div className="flex w-full min-w-0 flex-col gap-2">
                    {suggestedQuestions.map((question, index) => (
                      <button
                        key={`${index}-${question}`}
                        type="button"
                        disabled={loading}
                        onClick={() => sendUserMessage(question)}
                        className="min-w-0 whitespace-normal break-words rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {question}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                    message.role === "user"
                      ? "bg-slate-900 text-white"
                      : "bg-slate-100 text-slate-900"
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
              </div>
            ))}

            {loading && (
              <div className="text-sm text-slate-500" aria-live="polite">
                Thinking...
              </div>
            )}
          </div>

          {showBookingForm && clientSlug && (
            <div className="max-h-[min(45vh,360px)] shrink-0 overflow-y-auto border-t border-slate-200 bg-white px-3 py-3">
              <BookingForm
                client={clientSlug}
                threadId={threadId}
                needsConsent={!known}
                consentOffer={offer}
                onSuccess={handleBookingSuccess}
                onCancel={() => setShowBookingForm(false)}
              />
            </div>
          )}

          <form
            onSubmit={handleSubmit}
            className="shrink-0 border-t border-slate-200 bg-white p-3"
          >
            {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
            <div className="flex gap-2">
              <label htmlFor="chat-message-input" className="sr-only">
                Type your question
              </label>
              <input
                id="chat-message-input"
                aria-label="Type your question"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={chatConfig.inputPlaceholder}
                className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-base outline-none ring-slate-400 focus:ring-2"
                disabled={loading}
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="shrink-0 rounded-xl bg-[#6B7280] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#4B5563] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Send
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
}
