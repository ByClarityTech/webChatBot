"use client";

import { FormEvent, useCallback, useState } from "react";

import type { ConsentOffer } from "@/lib/api";
import { createBooking } from "@/lib/booking-api";

import { SlotPicker } from "./slot-picker";

type BookingFormProps = {
  /** Slug of the client whose widget this is. */
  client: string;
  threadId: string | null;
  /** False when the visitor already shared details in this conversation. */
  needsConsent: boolean;
  /** The approved wording for the consent box; null means booking needs consent that is absent. */
  consentOffer: ConsentOffer | null;
  onSuccess: (message: string) => void;
  onCancel?: () => void;
};

function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

export function BookingForm({
  client,
  threadId,
  needsConsent,
  consentOffer,
  onSuccess,
  onCancel,
}: BookingFormProps) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const [currentPatient, setCurrentPatient] = useState<boolean | null>(null);
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const handleSlotSelect = useCallback((slotStart: string) => {
    setSelectedSlot(slotStart || null);
    setError(null);
  }, []);

  // Fail closed: a visitor who has not shared details cannot book without the consent wording.
  const consentBlocked = needsConsent && !consentOffer;
  const consentReady = !needsConsent || (consentOffer !== null && currentPatient !== null && consent);

  function validateFields(): boolean {
    const errors: Record<string, string> = {};
    const trimmedName = name.trim();
    const trimmedPhone = phone.trim();
    const trimmedEmail = email.trim();

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
    if (!selectedSlot) {
      errors.slot = "Please select a time slot";
    }
    if (needsConsent && consentOffer && !consent) {
      errors.consent = "Tick the box to agree to be contacted about this booking";
    }

    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (!validateFields() || consentBlocked) {
      return;
    }

    setSubmitting(true);

    try {
      const response = await createBooking({
        client,
        name: name.trim(),
        phone: phone.trim(),
        email: email.trim(),
        slot_start: selectedSlot!,
        thread_id: threadId,
        consent_accepted: needsConsent ? consent : false,
        consent_text_version: needsConsent && consentOffer ? consentOffer.version : null,
        is_current_patient: needsConsent ? currentPatient : null,
      });
      onSuccess(response.message);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Booking failed");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    !submitting &&
    !consentBlocked &&
    name.trim().length > 0 &&
    phone.trim().length >= 7 &&
    isValidEmail(email.trim()) &&
    Boolean(selectedSlot) &&
    consentReady;

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-3">
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
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setFieldErrors((current) => ({ ...current, name: "" }));
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
            value={phone}
            onChange={(event) => {
              setPhone(event.target.value);
              setFieldErrors((current) => ({ ...current, phone: "" }));
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
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setFieldErrors((current) => ({ ...current, email: "" }));
            }}
            autoComplete="email"
            className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
            disabled={submitting}
          />
          {fieldErrors.email && <p className="mt-1 text-xs text-red-600">{fieldErrors.email}</p>}
        </div>
      </div>

      <SlotPicker client={client} selectedSlot={selectedSlot} onSelect={handleSlotSelect} />
      {fieldErrors.slot && <p className="text-xs text-red-600">{fieldErrors.slot}</p>}

      {needsConsent && (
        <fieldset disabled={submitting} className="space-y-3">
          {consentOffer ? (
            <>
              <div>
                <legend className="mb-1 text-xs font-medium text-slate-600">
                  Are you a current patient?
                </legend>
                <div className="flex gap-4 text-sm text-slate-700">
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="booking-current-patient"
                      checked={currentPatient === true}
                      onChange={() => setCurrentPatient(true)}
                    />
                    Yes
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="booking-current-patient"
                      checked={currentPatient === false}
                      onChange={() => setCurrentPatient(false)}
                    />
                    No
                  </label>
                </div>
              </div>
              <label className="flex items-start gap-2 rounded-xl border border-slate-200 bg-white p-3 text-xs leading-relaxed text-slate-700">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(event) => {
                    setConsent(event.target.checked);
                    setFieldErrors((current) => ({ ...current, consent: "" }));
                  }}
                  className="mt-0.5"
                />
                <span>{consentOffer.text}</span>
              </label>
              {fieldErrors.consent && (
                <p className="text-xs text-red-600">{fieldErrors.consent}</p>
              )}
            </>
          ) : (
            <p className="text-xs text-slate-600" role="status">
              Online booking needs your consent to be contacted, and that wording is not available
              right now. Please call our office to book.
            </p>
          )}
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
