"use client";

import { useEffect, useRef, useState } from "react";

import { AvailabilitySlot, getAvailability } from "@/lib/booking-api";

type SlotPickerProps = {
  /** Slug of the client whose open times to show. */
  client: string;
  /** The day shown. Owned by the parent so it survives a conflict. */
  date: string;
  onDateChange: (date: string) => void;
  selectedSlot: string | null;
  onSelect: (slotStart: string | null) => void;
  /** Bump to reload the day (after a conflict, the times may have changed). */
  refreshKey: number;
};

export function todayDateString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function formatSlotLabel(start: string, end: string, timezone: string): string {
  const timeOptions: Intl.DateTimeFormatOptions = {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: timezone,
  };
  const startLabel = new Date(start).toLocaleTimeString("en-US", timeOptions);
  const endLabel = new Date(end).toLocaleTimeString("en-US", timeOptions);
  return `${startLabel} – ${endLabel}`;
}

export function SlotPicker({
  client,
  date,
  onDateChange,
  selectedSlot,
  onSelect,
  refreshKey,
}: SlotPickerProps) {
  const [timezone, setTimezone] = useState<string>("UTC");
  const [slots, setSlots] = useState<AvailabilitySlot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The effect reads these without re-running when they change.
  const selectedRef = useRef(selectedSlot);
  selectedRef.current = selectedSlot;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    let cancelled = false;

    async function loadSlots() {
      setLoading(true);
      setError(null);
      try {
        const response = await getAvailability(client, date);
        if (cancelled) {
          return;
        }
        setTimezone(response.timezone);
        setSlots(response.slots);
        // Keep the visitor's choice only while it is still open.
        const current = selectedRef.current;
        if (current && !response.slots.some((slot) => slot.start === current)) {
          onSelectRef.current(null);
        }
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setSlots([]);
        setError(loadError instanceof Error ? loadError.message : "Failed to load slots");
        onSelectRef.current(null);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadSlots();

    return () => {
      cancelled = true;
    };
  }, [client, date, refreshKey]);

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor="booking-date" className="mb-1 block text-xs font-medium text-slate-600">
          Date
        </label>
        <input
          id="booking-date"
          type="date"
          min={todayDateString()}
          value={date}
          onChange={(event) => onDateChange(event.target.value)}
          className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
        />
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-slate-600">
          Available times{slots.length > 0 ? ` (${timezone})` : ""}
        </p>

        {loading && (
          <p className="text-sm text-slate-500" aria-live="polite">
            Loading slots...
          </p>
        )}

        {!loading && error && (
          <p className="text-sm text-red-600" role="alert">
            {error}
          </p>
        )}

        {!loading && !error && slots.length === 0 && (
          <p className="text-sm text-slate-500">No slots available for this date.</p>
        )}

        {!loading && !error && slots.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {slots.map((slot) => {
              const isSelected = selectedSlot === slot.start;
              return (
                <button
                  key={slot.start}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => onSelect(slot.start)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                    isSelected
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 bg-white text-slate-800 hover:border-slate-400"
                  }`}
                >
                  {formatSlotLabel(slot.start, slot.end, timezone)}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
