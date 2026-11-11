"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import {
  ChatUnavailableError,
  clientSlugFromUrl,
  getWidgetConfig,
  sendMessage,
} from "@/lib/api";
import { chatConfig, MAX_SUGGESTED_QUESTIONS } from "@/lib/chat-config";

import { BookingForm } from "./booking-form";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

// Per client, so two clients' widgets in one browser never share a conversation.
function threadStorageKey(client: string): string {
  return `website-assistant-thread-id:${client}`;
}

function messagesStorageKey(client: string): string {
  return `website-assistant-messages:${client}`;
}

function loadStoredMessages(client: string): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(messagesStorageKey(client));
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(
      (item): item is ChatMessage =>
        typeof item === "object" &&
        item !== null &&
        typeof item.id === "string" &&
        (item.role === "user" || item.role === "assistant") &&
        typeof item.content === "string",
    );
  } catch {
    return [];
  }
}

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

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [showBookingForm, setShowBookingForm] = useState(false);
  // The client whose assistant this is, from the page URL (?client=...).
  const [clientSlug, setClientSlug] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  // The client's display name, from the widget config; shown in the header.
  const [clientName, setClientName] = useState<string | null>(null);
  // The practice's starred default questions (PRD FR-07), from the widget config.
  const [practiceQuestions, setPracticeQuestions] = useState<string[]>([]);
  // True once the widget config request has finished, either way. Until then no chips are
  // shown, so the fallback never flashes before the practice's own questions arrive.
  const [configChecked, setConfigChecked] = useState(false);

  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);

  useEffect(() => {
    const slug = clientSlugFromUrl(window.location.search);
    setClientSlug(slug);
    if (!slug) {
      setUnavailable(true);
    } else {
      const storedThread = sessionStorage.getItem(threadStorageKey(slug));
      if (storedThread) {
        setThreadId(storedThread);
      }
      setMessages(loadStoredMessages(slug));
      // Checks the client before the visitor types, and loads its name and questions.
      getWidgetConfig(slug)
        .then((config) => {
          setClientName(config.name);
          setPracticeQuestions(cleanSuggestedQuestions(config.default_questions));
          setConfigChecked(true);
        })
        .catch((configError) => {
          if (configError instanceof ChatUnavailableError) {
            setUnavailable(true);
          }
          // Any other failure: chat still works, with the fallback questions and no name.
          setConfigChecked(true);
        });
    }
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated || !clientSlug) {
      return;
    }
    if (messages.length === 0) {
      sessionStorage.removeItem(messagesStorageKey(clientSlug));
      return;
    }
    sessionStorage.setItem(messagesStorageKey(clientSlug), JSON.stringify(messages));
  }, [messages, hydrated, clientSlug]);

  useEffect(() => {
    if (!hydrated || !clientSlug) {
      return;
    }
    if (threadId) {
      sessionStorage.setItem(threadStorageKey(clientSlug), threadId);
    } else {
      sessionStorage.removeItem(threadStorageKey(clientSlug));
    }
  }, [threadId, hydrated, clientSlug]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    const hasNewMessages = messages.length > prevMessageCountRef.current;

    if (container && hasNewMessages) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }

    prevMessageCountRef.current = messages.length;
  }, [messages, loading, showBookingForm]);

  function handleBookingSuccess(message: string) {
    setShowBookingForm(false);
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: "assistant",
        content: message,
      },
    ]);
  }

  async function sendUserMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading || !clientSlug) {
      return;
    }

    setError(null);
    setLoading(true);
    setInput("");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: trimmed },
    ]);

    try {
      const response = await sendMessage(trimmed, clientSlug, threadId);
      setThreadId(response.thread_id);
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
        setUnavailable(true);
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

  if (unavailable) {
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
    </div>
  );
}
