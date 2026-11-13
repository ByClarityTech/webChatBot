"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import type { FallbackCard as FallbackCardData } from "@/lib/api";
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
import { leaveLive, LiveMessage, LiveSnapshot, openLiveStream, requestLive } from "@/lib/live-chat-api";
import { consumeSse } from "@/lib/sse";

import { AvailabilitySlot, BookingError, createBooking, getSuggestions } from "@/lib/booking-api";

import { BookingDraft, BookingForm } from "./booking-form";
import { FallbackCard } from "./fallback-card";
import { ShareDetailsForm, ShareValues } from "./share-details-form";
import { todayDateString } from "./slot-picker";

function emptyBookingDraft(): BookingDraft {
  return {
    date: todayDateString(),
    name: "",
    phone: "",
    email: "",
    isCurrentPatient: null,
    consent: false,
    selectedSlot: null,
  };
}

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Contact options under an answer the knowledge base could not give. */
  fallback?: FallbackCardData;
  /** A message a staff member typed in the live handover. */
  speaker?: "staff";
  /** Tappable related-question chips under this answer. */
  related?: string[];
};

const DEFAULT_HEADER_COLOR = "#1D4ED8";

type WidgetBranding = {
  botName: string | null;
  greeting: string | null;
  headerColor: string;
  headerTextColor: string;
  logoUrl: string | null;
};

const LIVE_WAITING_MESSAGE ="Connecting you to a member of our team. Please wait a moment.";
const LIVE_JOINED_MESSAGE = "A member of our team has joined the chat.";
const LIVE_NOBODY_NOTICE =
  "No one from our team is online right now. Leave your details and the office will call you back.";
const LIVE_TIMEOUT_NOTICE =
  "No one was able to reply in time. Leave your details and the office will call you back.";
const LIVE_ENDED_NOTICE = "The chat with our team has ended. You can keep asking questions here.";
const LIVE_LOST_NOTICE = "We lost the connection to our team. You can keep asking questions here.";
const LIVE_LEFT_NOTICE = "You left the chat with our team.";
const CALLBACK_SAVED_NOTICE = "Thank you. The office has your details for a callback.";

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
  // The practice's look, from the widget config. Null until loaded: the defaults show meanwhile.
  const [branding, setBranding] = useState<WidgetBranding | null>(null);
  // The booking card. The draft lives here, so a failed or conflicting submit keeps every field.
  const [bookingDraft, setBookingDraft] = useState<BookingDraft>(emptyBookingDraft);
  const [bookingSubmitting, setBookingSubmitting] = useState(false);
  const [bookingError, setBookingError] = useState<string | null>(null);
  const [bookingSuggestions, setBookingSuggestions] = useState<{
    slots: AvailabilitySlot[];
    timezone: string;
  }>({ slots: [], timezone: "UTC" });
  const [slotRefreshKey, setSlotRefreshKey] = useState(0);
  // The answer to "Are you a current patient?" from earlier in this conversation, if any.
  const [knownCurrentPatient, setKnownCurrentPatient] = useState<boolean | null>(null);
  // The practice's starred default questions (PRD FR-07), from the widget config.
  const [practiceQuestions, setPracticeQuestions] = useState<string[]>([]);
  // True once the widget config request has finished, either way.
  const [configChecked, setConfigChecked] = useState(false);
  // The approved consent wording. Null means no sharing is offered (fail closed).
  const [offer, setOffer] = useState<ConsentOffer | null>(null);
  // The share form serves two purposes: sharing details, or asking for a callback.
  const [shareKind, setShareKind] = useState<"share_contact" | "callback">("share_contact");
  const [callbackOffer, setCallbackOffer] = useState<ConsentOffer | null>(null);
  // Live handover with a person. The token stays in memory: never in storage or in a URL.
  const [liveMode, setLiveMode] = useState<"idle" | "waiting" | "live">("idle");
  const liveRef = useRef<{ handoverId: string; token: string; controller: AbortController } | null>(null);
  const seenStaffIds = useRef<Set<string>>(new Set());

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
            setKnownCurrentPatient(status.current_patient ?? null);
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
        setBranding({
          botName: config.bot_name,
          greeting: config.greeting,
          headerColor: config.header_color,
          headerTextColor: config.header_text_color,
          logoUrl: config.logo_url,
        });
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
    setBookingDraft(emptyBookingDraft());
    setBookingError(null);
    setKnownCurrentPatient(null);
    endLiveLocally();
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
    setBookingDraft(emptyBookingDraft());
    setBookingError(null);
    endLiveLocally();
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
      setShareKind("share_contact");
      setPhase("share");
    }
  }

  /** The callback capture, inside the conversation that the answer came from. */
  async function chooseCallback() {
    if (!clientSlug || !threadId || !sessionId) {
      setNotice("Start a conversation first, then ask for a callback.");
      return;
    }
    setNotice(null);
    const wording = await getConsentOffer(clientSlug, "callback").catch(() => null);
    if (!wording) {
      setNotice("Callbacks are not available right now. Please call or text the office.");
      return;
    }
    setCallbackOffer(wording);
    setShareKind("callback");
    setShareError(null);
    setPhase("share");
  }

  async function handleShareSubmit(values: ShareValues) {
    const activeOffer = shareKind === "callback" ? callbackOffer : offer;
    if (!activeOffer || !clientSlug || !threadId || !sessionId) {
      return;
    }
    setShareSubmitting(true);
    setShareError(null);
    try {
      await submitShareConsent({
        client: clientSlug,
        thread_id: threadId,
        session_id: sessionId,
        purpose: shareKind,
        text_version: activeOffer.version,
        full_name: values.full_name,
        phone: values.phone,
        is_current_patient: values.is_current_patient,
        consent_accepted: true,
      });
      setKnown(true);
      if (shareKind === "callback") {
        setNotice(CALLBACK_SAVED_NOTICE);
      }
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

  /** The quick picks for the booking card. Never blocks the card: no picks is a normal state. */
  async function refreshSuggestions() {
    if (!clientSlug) {
      return;
    }
    const result = await getSuggestions(clientSlug);
    setBookingSuggestions({ slots: result.slots, timezone: result.timezone });
  }

  async function handleBookingSubmit(draft: BookingDraft) {
    if (!clientSlug || !threadId || !sessionId || !draft.selectedSlot) {
      return;
    }
    if (draft.isCurrentPatient === null) {
      return;
    }
    const needsConsent = !known;
    setBookingSubmitting(true);
    setBookingError(null);
    try {
      const response = await createBooking({
        client: clientSlug,
        name: draft.name.trim(),
        phone: draft.phone.trim(),
        email: draft.email.trim(),
        slot_start: draft.selectedSlot,
        thread_id: threadId,
        is_current_patient: draft.isCurrentPatient,
        consent_accepted: needsConsent ? draft.consent : false,
        consent_text_version: needsConsent && offer ? offer.version : null,
      });
      setShowBookingForm(false);
      setBookingDraft(emptyBookingDraft());
      // A booking with consent also makes the conversation known from here on.
      if (!known) {
        setKnown(true);
      }
      setKnownCurrentPatient(draft.isCurrentPatient);
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: response.message },
      ]);
    } catch (submitError) {
      if (submitError instanceof BookingError && submitError.code === "SESSION_ENDED") {
        returnToChoice(CLOSED_NOTICE);
        return;
      }
      if (submitError instanceof BookingError && submitError.code === "SLOT_TAKEN") {
        // Only the taken time is cleared. Name, phone, email and the other answers stay.
        setBookingDraft((current) => ({ ...current, selectedSlot: null }));
        setSlotRefreshKey((key) => key + 1);
        setBookingError("That time was just taken. Pick another time below. Your details are kept.");
        void refreshSuggestions();
        return;
      }
      setBookingError(
        submitError instanceof Error ? submitError.message : "Booking failed. Try again.",
      );
    } finally {
      setBookingSubmitting(false);
    }
  }

  /** A chip under an answer: asked as the visitor's next message, and the chips go away. */
  function chooseRelated(messageId: string, question: string) {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId ? { ...message, related: [] } : message,
      ),
    );
    void sendUserMessage(question);
  }

  // Drop the live connection on this side. The server ends the handover on its own terms.
  function endLiveLocally() {
    liveRef.current?.controller.abort();
    liveRef.current = null;
    setLiveMode("idle");
  }

  useEffect(() => () => liveRef.current?.controller.abort(), []);

  function addStaffMessage(message: LiveMessage) {
    if (seenStaffIds.current.has(message.id)) {
      return;
    }
    seenStaffIds.current.add(message.id);
    setMessages((current) => [
      ...current,
      { id: message.id, role: "assistant", speaker: "staff", content: message.body },
    ]);
  }

  function onLiveEvent(event: string, data: unknown) {
    if (event === "state") {
      const snapshot = data as LiveSnapshot;
      if (snapshot.handover.status === "accepted") {
        setLiveMode("live");
      } else if (snapshot.handover.status === "ended" || snapshot.handover.status === "timed_out") {
        endLiveLocally();
        setNotice(LIVE_ENDED_NOTICE);
      }
      snapshot.messages.filter((message) => message.sender === "staff").forEach(addStaffMessage);
    } else if (event === "handover_accepted") {
      setLiveMode("live");
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: LIVE_JOINED_MESSAGE },
      ]);
    } else if (event === "agent_message") {
      addStaffMessage(data as LiveMessage);
    } else if (event === "handover_timeout") {
      endLiveLocally();
      void chooseCallback();
      setNotice(LIVE_TIMEOUT_NOTICE);
    } else if (event === "handover_ended") {
      endLiveLocally();
      setNotice(LIVE_ENDED_NOTICE);
    }
  }

  function followLive(handoverId: string, token: string) {
    if (!clientSlug) {
      return;
    }
    const controller = new AbortController();
    liveRef.current = { handoverId, token, controller };
    openLiveStream(clientSlug, handoverId, token, controller.signal)
      .then((response) => consumeSse(response, onLiveEvent, controller.signal))
      .catch(() => undefined)
      .finally(() => {
        // The stream closed without a final event: the connection dropped.
        if (!controller.signal.aborted && liveRef.current?.controller === controller) {
          liveRef.current = null;
          setLiveMode("idle");
          setNotice(LIVE_LOST_NOTICE);
        }
      });
  }

  /** Asks for a person. Nobody online means a callback instead. */
  async function requestLiveChat() {
    if (!clientSlug || !threadId || !sessionId || liveRef.current) {
      return;
    }
    setNotice(null);
    try {
      const result = await requestLive(clientSlug, threadId, sessionId);
      if (result.status === "callback" || !result.handover_id || !result.token) {
        void chooseCallback();
        setNotice(LIVE_NOBODY_NOTICE);
        return;
      }
      seenStaffIds.current = new Set();
      setLiveMode("waiting");
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: LIVE_WAITING_MESSAGE },
      ]);
      followLive(result.handover_id, result.token);
    } catch (liveError) {
      if (liveError instanceof ApiRequestError && liveError.status === 409) {
        returnToChoice(CLOSED_NOTICE);
      } else {
        setNotice(
          liveError instanceof Error
            ? liveError.message
            : "Could not reach our team. Please call or text the office.",
        );
      }
    }
  }

  function leaveLiveChat() {
    const live = liveRef.current;
    if (!live || !clientSlug) {
      return;
    }
    void leaveLive(clientSlug, live.handoverId, live.token);
    endLiveLocally();
    setNotice(LIVE_LEFT_NOTICE);
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
      // While a person is handling the chat, the server may return no reply text.
      if (response.reply.trim()) {
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: response.reply,
            fallback: response.fallback ?? undefined,
            // Chips only after an answer from the model or an approved FAQ.
            related:
              response.answer_source === "model" || response.answer_source === "faq"
                ? (response.related_questions ?? []).slice(0, 3)
                : undefined,
          },
        ]);
      }
      if (response.action === "open_booking_form") {
        setShowBookingForm(true);
        // Prefill the answer when the conversation already holds it; never overwrite a choice.
        setBookingDraft((current) => ({
          ...current,
          isCurrentPatient: current.isCurrentPatient ?? knownCurrentPatient,
        }));
        void refreshSuggestions();
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

  const headerColor = branding?.headerColor ?? DEFAULT_HEADER_COLOR;
  const headerTextColor = branding?.headerTextColor ?? "#FFFFFF";
  // The practice's greeting when it has saved one; otherwise the env default.
  const greetingText = branding?.greeting || chatConfig.greeting;
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
      <div
        className="flex shrink-0 items-center gap-3 px-4 py-3"
        style={{ backgroundColor: headerColor, color: headerTextColor }}
      >
        <img
          src={branding?.logoUrl ?? "/bot-profile.png"}
          alt=""
          className="h-11 w-11 shrink-0 rounded-full border border-white bg-white object-contain p-0.5"
        />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[15px] font-bold leading-tight">
            {branding?.botName || clientName || ""}
          </span>
          <span className="text-xs leading-tight">AI Assistant</span>
        </div>
      </div>

      {notice && (
        <p className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900" role="status">
          {notice}
        </p>
      )}

      {liveMode !== "idle" && (
        <div
          className="flex shrink-0 items-center justify-between gap-2 bg-emerald-50 px-4 py-2 text-xs text-emerald-900"
          role="status"
        >
          <span>{liveMode === "waiting" ? "Waiting for a member of our team..." : "You are chatting with our team."}</span>
          <button type="button" onClick={leaveLiveChat} className="shrink-0 underline">
            Leave chat
          </button>
        </div>
      )}

      {phase === "choice" && (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 overflow-y-auto p-4">
          {greetingText && (
            <p className="text-center text-sm leading-relaxed text-slate-500">
              {greetingText}
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

      {phase === "share" && (shareKind === "callback" ? callbackOffer : offer) && (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <ShareDetailsForm
            title={shareKind === "callback" ? "Request a callback" : "Share my details"}
            consentText={(shareKind === "callback" ? callbackOffer : offer)?.text ?? ""}
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
                {greetingText && (
                  <p className="text-center text-sm leading-relaxed text-slate-500">
                    {greetingText}
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
                className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}
              >
                {message.speaker === "staff" && (
                  <p className="mb-1 text-xs font-medium text-slate-500">Our team</p>
                )}
                <div
                  className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                    message.role === "user"
                      ? "bg-slate-900 text-white"
                      : "bg-slate-100 text-slate-900"
                  }`}
                >
                  <p className="whitespace-pre-wrap break-words">{message.content}</p>
                </div>
                {message.fallback && (
                  <FallbackCard
                    card={message.fallback}
                    onCallback={chooseCallback}
                    onLiveChat={requestLiveChat}
                    disabled={loading}
                  />
                )}
                {message.related && message.related.length > 0 && (
                  <div className="mt-2 flex max-w-[85%] flex-wrap gap-2" aria-label="Related questions">
                    {message.related.map((question) => (
                      <button
                        key={question}
                        type="button"
                        disabled={loading}
                        onClick={() => chooseRelated(message.id, question)}
                        className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-left text-xs text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {question}
                      </button>
                    ))}
                  </div>
                )}
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
                draft={bookingDraft}
                onDraftChange={(patch) => setBookingDraft((current) => ({ ...current, ...patch }))}
                needsConsent={!known}
                consentOffer={offer}
                suggestions={bookingSuggestions.slots}
                suggestionTimezone={bookingSuggestions.timezone}
                refreshKey={slotRefreshKey}
                submitting={bookingSubmitting}
                error={bookingError}
                onSubmit={handleBookingSubmit}
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
