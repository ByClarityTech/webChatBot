"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { ChatUnavailableError, clientSlugFromUrl, sendMessage } from "@/lib/api";
import { chatConfig } from "@/lib/chat-config";

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

  const showHeader = Boolean(chatConfig.title || chatConfig.subtitle);
  const isEmpty = messages.length === 0 && !loading;

  if (unavailable) {
    // No or unknown client, or the client is suspended: nothing to chat with.
    return (
      <div
        className={
          isEmbed
            ? "flex h-full min-h-0 flex-col items-center justify-center bg-white p-6"
            : "flex h-[min(640px,80vh)] flex-col items-center justify-center rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
        }
      >
        <p className="text-center text-sm text-slate-500" role="status">
          This assistant is not available.
        </p>
      </div>
    );
  }

  return (
    <div
      className={
        isEmbed
          ? "flex h-full min-h-0 flex-col bg-white"
          : "flex h-[min(640px,80vh)] flex-col rounded-2xl border border-slate-200 bg-white shadow-sm"
      }
    >
      {showHeader && (
        <div
          style={{
            background: "linear-gradient(135deg, rgb(43, 60, 247), rgb(91, 142, 255))",
            padding: "8px 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
            borderTopLeftRadius: "16px",
            borderTopRightRadius: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div
              style={{
                width: "50px",
                height: "50px",
                borderRadius: "50%",
                background: "rgb(255, 255, 255)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <img src="/bot-profile.png" alt="Sentiq" style={{ width: "100%", height: "100%" }} />
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span
                style={{
                  color: "rgb(255, 255, 255)",
                  fontSize: "15px",
                  fontWeight: 600,
                  letterSpacing: "0.02em",
                  lineHeight: 1.2,
                }}
              >
                Sentiq
              </span>
              <span
                style={{
                  color: "rgba(255, 255, 255, 0.75)",
                  fontSize: "11px",
                  fontWeight: 400,
                  letterSpacing: "0.01em",
                  lineHeight: 1.2,
                }}
              >
                AI Assistant
              </span>
            </div>
          </div>
          <button
            aria-label="Close chat"
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "4px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "6px",
              transition: "background 0.2s",
            }}
          />
        </div>
      )}

      <div
        ref={messagesContainerRef}
        className={`min-h-0 flex-1 overscroll-contain overflow-y-auto p-3 ${
          isEmpty ? "flex items-center justify-center px-4" : "space-y-3 px-4 py-3"
        }`}
      >
        {isEmpty && (
          <div className="flex w-full max-w-sm flex-col items-center gap-4">
            {chatConfig.greeting && (
              <p className="text-center text-sm leading-relaxed text-slate-500">
                {chatConfig.greeting}
              </p>
            )}
            {chatConfig.suggestedQuestions.length > 0 && (
              <div className="flex w-full flex-col gap-2">
                {chatConfig.suggestedQuestions.map((question) => (
                  <button
                    key={question}
                    type="button"
                    disabled={loading}
                    onClick={() => sendUserMessage(question)}
                    className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
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
              <p className="whitespace-pre-wrap">{message.content}</p>
            </div>
          </div>
        ))}

        {loading && (
          <div className="text-sm text-slate-500" aria-live="polite">
            Thinking...
          </div>
        )}
      </div>

      {showBookingForm && (
        <div className="max-h-[min(45vh,360px)] shrink-0 overflow-y-auto border-t border-slate-200 bg-white px-3 py-3">
          <BookingForm
            threadId={threadId}
            onSuccess={handleBookingSuccess}
            onCancel={() => setShowBookingForm(false)}
          />
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className={`shrink-0 border-t border-slate-200 bg-white ${isEmbed ? "p-3" : "p-2"}`}
        style={{
          borderBottomLeftRadius: isEmbed ? undefined : "16px",
          borderBottomRightRadius: isEmbed ? undefined : "16px",
        }}
      >
        {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={chatConfig.inputPlaceholder}
            className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-base outline-none ring-slate-400 focus:ring-2"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="shrink-0 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
