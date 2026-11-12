"use client";

import type { FallbackCard as FallbackCardData } from "@/lib/api";

type FallbackCardProps = {
  card: FallbackCardData;
  /** Opens the callback capture (share details with the callback wording). */
  onCallback: () => void;
  /** Live chat is not available yet; the chat panel explains that. */
  onLiveChat: () => void;
  disabled?: boolean;
};

// Only the options the server allowed are drawn. Every button has a text label, so a screen
// reader reads what it does, and the links are real links so a phone opens the dialer or texts.
const actionClass =
  "inline-flex min-h-[40px] items-center justify-center rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#1D4ED8] disabled:cursor-not-allowed disabled:opacity-50";

export function FallbackCard({ card, onCallback, onLiveChat, disabled = false }: FallbackCardProps) {
  const hasAny = card.call || card.text || card.callback || card.live_chat;
  if (!hasAny) {
    return null;
  }
  return (
    <div className="mt-2 flex max-w-[85%] flex-col gap-2 rounded-2xl border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap gap-2">
        {card.call && (
          <a href={card.call} className={actionClass} aria-disabled={disabled || undefined}>
            {card.labels.call}
          </a>
        )}
        {card.text && (
          <a href={card.text} className={actionClass} aria-disabled={disabled || undefined}>
            {card.labels.text}
          </a>
        )}
      </div>
      {card.callback && (
        <button type="button" onClick={onCallback} disabled={disabled} className={actionClass}>
          {card.labels.callback}
        </button>
      )}
      {card.live_chat && (
        <button type="button" onClick={onLiveChat} disabled={disabled} className={actionClass}>
          {card.labels.live_chat}
        </button>
      )}
    </div>
  );
}
