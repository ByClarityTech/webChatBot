"use client";

import { FormEvent, useState } from "react";

import type { ConsentOffer } from "@/lib/api";
import type { AvailabilitySlot } from "@/lib/booking-api";

import { formatSlotLabel, SlotPicker } from "./slot-picker";

/**
 * Everything the visitor has typed or chosen in the booking card. Owned by the chat panel, so a
 * failed or conflicting submit never clears it.
 */
export type BookingDraft = {
  date: string;
  name: string;
  phone: string;
  email: string;
  isCurrentPatient: boolean | null;
  consent: boolean;
  selectedSlot: string | null;
};

type BookingFormProps = {
  /** Slug of the client whose widget this is. */
  client: string;
  draft: BookingDraft;
  onDraftChange: (patch: Partial<BookingDraft>) => void;
  /** False when the visitor already shared details in this conversation. */
  needsConsent: boolean;
  /** The approved consent wording; null while booking needs it means booking is blocked. */
  consentOffer: ConsentOffer | null;
  /** Up to three quick picks, from the next seven days. */
  suggestions: AvailabilitySlot[];
  suggestionTimezone: string;
  /** Bump to reload the day picker (after a conflict). */
  refreshKey: number;
  submitting: boolean;
  error: string | null;
  onSubmit: (draft: BookingDraft) => void;
  onCancel?: () => void;
};

function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

export function BookingForm({
  client,
  draft,
  onDraftChange,
  needsConsent,
  consentOffer,
  suggestions,
  suggestionTimezone,
  refreshKey,
  submitting,
  error,
  onSubmit,
  onCancel,
}: BookingFormProps) {
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Fail closed: a visitor who has not shared details cannot book without the consent wording.
  const consentBlocked = needsConsent && !consentOffer;

  function clearFieldError(key: string) {
    setFieldErrors((current) => ({ ...current, [key]: "" }));
  }

  function validate(values: BookingDraft): boolean {
    const errors: Record<string, string> = {};
    const trimmedName = values.name.trim();
    const trimmedPhone = values.phone.trim();
    const trimmedEmail = values.email.trim();

    if (!trimmedName) {
      errors.name = "Name is required";
    }
    if (!trimmedPhone) {
      errors.phone = "Phone is required";
    } else if (trimmedPhone.length < 7) {
      errors.phone = "Phone must be at least 7 characters";
    }
    if (!trimmedEmail) {
      errors.email = "Email is required";
    } else if (!isValidEmail(trimmedEmail)) {
      errors.email = "Enter a valid email address";
    }
    if (!values.selectedSlot) {
      errors.slot = "Please select a time slot";
    }
    if (values.isCurrentPatient === null) {
      errors.currentPatient = "Please tell us whether you are a current patient";
    }
    if (needsConsent && consentOffer && !values.consent) {
      errors.consent = "Tick the box to agree to be contacted about this booking";
    }

    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (consentBlocked || submitting) {
      return;
    }
    if (validate(draft)) {
      onSubmit(draft);
    }
  }

  const canSubmit =
    !submitting &&
    !consentBlocked &&
    draft.name.trim().length > 0 &&
    draft.phone.trim().length >= 7 &&
    isValidEmail(draft.email.trim()) &&
    Boolean(draft.selectedSlot) &&
    draft.isCurrentPatient !== null &&
    (!needsConsent || (consentOffer !== null && draft.consent));

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-slate-900">Book a meeting</p>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="shrink-0 text-xs text-slate-500 underline-offset-2 hover:text-slate-700 hover:underline"
          >
            Cancel
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="booking-name" className="mb-1 block text-xs font-medium text-slate-600">
            Name
          </label>
          <input
            id="booking-name"
            type="text"
            value={draft.name}
            onChange={(event) => {
              onDraftChange({ name: event.target.value });
              clearFieldError("name");
            }}
            autoComplete="name"
            className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
            disabled={submitting}
          />
          {fieldErrors.name && <p className="mt-1 text-xs text-red-600">{fieldErrors.name}</p>}
        </div>

        <div>
          <label htmlFor="booking-phone" className="mb-1 block text-xs font-medium text-slate-600">
            Phone
          </label>
          <input
            id="booking-phone"
            type="tel"
            value={draft.phone}
            onChange={(event) => {
              onDraftChange({ phone: event.target.value });
              clearFieldError("phone");
            }}
            autoComplete="tel"
            className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
            disabled={submitting}
          />
          {fieldErrors.phone && <p className="mt-1 text-xs text-red-600">{fieldErrors.phone}</p>}
        </div>

        <div>
          <label htmlFor="booking-email" className="mb-1 block text-xs font-medium text-slate-600">
            Email
          </label>
          <input
            id="booking-email"
            type="email"
            value={draft.email}
            onChange={(event) => {
              onDraftChange({ email: event.target.value });
              clearFieldError("email");
            }}
            autoComplete="email"
            className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
            disabled={submitting}
          />
          {fieldErrors.email && <p className="mt-1 text-xs text-red-600">{fieldErrors.email}</p>}
        </div>
      </div>

      <fieldset disabled={submitting} className="space-y-2">
        <legend className="mb-1 text-xs font-medium text-slate-600">
          Are you a current patient?
        </legend>
        <div className="flex gap-4 text-sm text-slate-700">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="booking-current-patient"
              checked={draft.isCurrentPatient === true}
              onChange={() => {
                onDraftChange({ isCurrentPatient: true });
                clearFieldError("currentPatient");
              }}
            />
            Yes
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="booking-current-patient"
              checked={draft.isCurrentPatient === false}
              onChange={() => {
                onDraftChange({ isCurrentPatient: false });
                clearFieldError("currentPatient");
              }}
            />
            No
          </label>
        </div>
        {fieldErrors.currentPatient && (
          <p className="text-xs text-red-600">{fieldErrors.currentPatient}</p>
        )}
      </fieldset>

      {suggestions.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-600">Next open times</p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((slot) => {
              const isSelected = draft.selectedSlot === slot.start;
              return (
                <button
                  key={slot.start}
                  type="button"
                  aria-pressed={isSelected}
                  disabled={submitting}
                  onClick={() => {
                    onDraftChange({ selectedSlot: slot.start });
                    clearFieldError("slot");
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                    isSelected
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 bg-white text-slate-800 hover:border-slate-400"
                  }`}
                >
                  {formatSlotLabel(slot.start, slot.end, suggestionTimezone)}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <SlotPicker
        client={client}
        date={draft.date}
        onDateChange={(date) => onDraftChange({ date })}
        selectedSlot={draft.selectedSlot}
        onSelect={(slotStart) => {
          onDraftChange({ selectedSlot: slotStart });
          clearFieldError("slot");
        }}
        refreshKey={refreshKey}
      />
      {fieldErrors.slot && <p className="text-xs text-red-600">{fieldErrors.slot}</p>}

      {needsConsent && (
        <fieldset disabled={submitting} className="space-y-3">
          {consentOffer ? (
            <label className="flex items-start gap-2 rounded-xl border border-slate-200 bg-white p-3 text-xs leading-relaxed text-slate-700">
              <input
                type="checkbox"
                checked={draft.consent}
                onChange={(event) => {
                  onDraftChange({ consent: event.target.checked });
                  clearFieldError("consent");
                }}
                className="mt-0.5"
              />
              <span>{consentOffer.text}</span>
            </label>
          ) : (
            <p className="text-xs text-slate-600" role="status">
              Online booking needs your consent to be contacted, and that wording is not available
              right now. Please call our office to book.
            </p>
          )}
          {fieldErrors.consent && <p className="text-xs text-red-600">{fieldErrors.consent}</p>}
        </fieldset>
      )}

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={!canSubmit}
        className="w-full rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? "Booking..." : "Confirm booking"}
      </button>
    </form>
  );
}
