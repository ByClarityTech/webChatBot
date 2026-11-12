"use client";

import { FormEvent, useState } from "react";

export type ShareValues = {
  full_name: string;
  phone: string;
  is_current_patient: boolean;
};

type ShareDetailsFormProps = {
  /** The approved wording, with the practice name already filled in. */
  consentText: string;
  submitting: boolean;
  error: string | null;
  onSubmit: (values: ShareValues) => void;
  onSkip: () => void;
};

/** US numbers only (PRD R-05): 10 digits, optionally with a leading country code 1. */
export function isUsPhone(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  const national = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  return national.length === 10 && !"01".includes(national[0]) && !"01".includes(national[3]);
}

const inputClass =
  "w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2";

export function ShareDetailsForm({
  consentText,
  submitting,
  error,
  onSubmit,
  onSkip,
}: ShareDetailsFormProps) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [currentPatient, setCurrentPatient] = useState<boolean | null>(null);
  const [consent, setConsent] = useState(false);

  const nameOk = name.trim().length > 0;
  const phoneOk = isUsPhone(phone);
  const canContinue = nameOk && phoneOk && currentPatient !== null && consent && !submitting;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canContinue || currentPatient === null) {
      return;
    }
    onSubmit({ full_name: name.trim(), phone: phone.trim(), is_current_patient: currentPatient });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
      <p className="text-sm font-medium text-slate-900">Share my details</p>

      <div>
        <label htmlFor="share-name" className="mb-1 block text-xs font-medium text-slate-600">
          Name
        </label>
        <input
          id="share-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoComplete="name"
          className={inputClass}
          disabled={submitting}
        />
      </div>

      <div>
        <label htmlFor="share-phone" className="mb-1 block text-xs font-medium text-slate-600">
          Mobile phone (US)
        </label>
        <input
          id="share-phone"
          type="tel"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          autoComplete="tel"
          aria-invalid={phone.length > 0 && !phoneOk}
          className={inputClass}
          disabled={submitting}
        />
        {phone.length > 0 && !phoneOk && (
          <p className="mt-1 text-xs text-red-600">Enter a 10-digit US phone number.</p>
        )}
      </div>

      <fieldset disabled={submitting}>
        <legend className="mb-1 text-xs font-medium text-slate-600">Are you a current patient?</legend>
        <div className="flex gap-4 text-sm text-slate-700">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="current-patient"
              checked={currentPatient === true}
              onChange={() => setCurrentPatient(true)}
            />
            Yes
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="current-patient"
              checked={currentPatient === false}
              onChange={() => setCurrentPatient(false)}
            />
            No
          </label>
        </div>
      </fieldset>

      <label className="flex items-start gap-2 rounded-xl border border-slate-200 bg-white p-3 text-xs leading-relaxed text-slate-700">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
          disabled={submitting}
          className="mt-0.5"
        />
        <span>{consentText}</span>
      </label>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onSkip}
          disabled={submitting}
          className="text-xs text-slate-500 underline-offset-2 hover:text-slate-700 hover:underline disabled:opacity-50"
        >
          Skip, chat without sharing
        </button>
        <button
          type="submit"
          disabled={!canContinue}
          className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Saving..." : "Continue"}
        </button>
      </div>
    </form>
  );
}
