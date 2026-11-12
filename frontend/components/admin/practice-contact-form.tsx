"use client";

import { FormEvent, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { canAccess } from "@/lib/admin-permissions";
import { getPracticePhone, savePracticePhone } from "@/lib/practice-contact-api";

/** The number behind the chat's Call and Text buttons. Blank removes the buttons. */
export function PracticeContactForm() {
  const actor = useAdminUser();
  const allowed = canAccess(actor.user_type, "practice_contact");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;

  const [phone, setPhone] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!allowed || needsClient) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getPracticePhone(withClient)
      .then((value) => {
        if (!cancelled) {
          setSaved(value);
          setPhone(value ?? "");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load the number.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // scope.scope is the only input that changes the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, needsClient, scope.scope]);

  if (!allowed) {
    return null;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const stored = await savePracticePhone(withClient, phone.trim());
      setSaved(stored);
      setPhone(stored ?? "");
      setSuccess(stored ? "Phone number saved." : "Phone number removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the number.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Office phone for the chat</h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          Visitors who get an answer the chatbot cannot give see Call and Text buttons with this
          number. Use a US number. Leave it blank to hide those buttons.
        </p>
      </div>
      <ClientPicker scope={scope} id="contact-client" />
      {needsClient ? (
        <p className="text-sm text-slate-500">Choose a client to edit its phone number.</p>
      ) : loading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : (
        <form onSubmit={handleSubmit} className="max-w-md space-y-3" noValidate>
          <div>
            <label htmlFor="office-phone" className="block text-sm font-medium text-slate-700">
              Office phone (US)
            </label>
            <input
              id="office-phone"
              type="tel"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              placeholder="(312) 555-0187"
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
              disabled={saving}
            />
            <p className="mt-1 text-xs text-slate-500">
              {saved ? `Stored as ${saved}.` : "No number saved yet."}
            </p>
          </div>
          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
          {success && !error && (
            <p className="text-sm text-green-700" role="status">
              {success}
            </p>
          )}
          <button
            type="submit"
            disabled={saving}
            className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save phone number"}
          </button>
        </form>
      )}
    </section>
  );
}
