"use client";

import { useEffect, useState } from "react";

import { getAvailability } from "@/lib/booking-api";

type SlotPickerProps = {
  selectedSlot: string | null;
  onSelect: (slotStart: string) => void;
};

function todayDateString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatSlotLabel(start: string, end: string, timezone: string): string {
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

export function SlotPicker({ selectedSlot, onSelect }: SlotPickerProps) {
  const [date, setDate] = useState(todayDateString);
  const [timezone, setTimezone] = useState<string>("UTC");
  const [slots, setSlots] = useState<Array<{ start: string; end: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSlots() {
      setLoading(true);
      setError(null);
      setSlots([]);
      onSelect("");

      try {
        const response = await getAvailability(date);
        if (cancelled) {
          return;
        }
        setTimezone(response.timezone);
        setSlots(response.slots);
        if (response.slots.length === 0) {
          onSelect("");
        }
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Failed to load slots");
        onSelect("");
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
  }, [date, onSelect]);

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
          onChange={(event) => setDate(event.target.value)}
          className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
        />
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-slate-600">Available times</p>

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
