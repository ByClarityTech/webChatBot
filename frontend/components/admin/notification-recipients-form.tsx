"use client";

import { FormEvent, useEffect, useState } from "react";

import { useAdminUser } from "@/components/admin/admin-session";
import { ClientPicker, useClientScope } from "@/components/admin/client-picker";
import { canAccess } from "@/lib/admin-permissions";
import {
  addNotificationRecipient,
  listNotificationRecipients,
  NotificationRecipient,
  removeNotificationRecipient,
  setNotificationRecipientEnabled,
} from "@/lib/notification-recipients-api";

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/** Staff who get an email when a booking or callback request comes in. Up to five. */
export function NotificationRecipientsForm() {
  const actor = useAdminUser();
  const allowed = canAccess(actor.user_type, "practice_contact");
  const scope = useClientScope();
  const { needsClient, withClient } = scope;

  const [recipients, setRecipients] = useState<NotificationRecipient[]>([]);
  const [limit, setLimit] = useState(5);
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!allowed || needsClient) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    listNotificationRecipients(withClient)
      .then((data) => {
        if (!cancelled) {
          setRecipients(data.recipients);
          setLimit(data.limit);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err, "Could not load the recipients."));
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

  async function run(action: () => Promise<void>, message: string) {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await action();
      setSuccess(message);
      const data = await listNotificationRecipients(withClient);
      setRecipients(data.recipients);
      setLimit(data.limit);
    } catch (err) {
      setError(errorText(err, "That did not work. Try again."));
    } finally {
      setBusy(false);
    }
  }

  function handleAdd(event: FormEvent) {
    event.preventDefault();
    const address = email.trim();
    if (!address) return;
    void run(async () => {
      await addNotificationRecipient(withClient, address);
      setEmail("");
    }, "Recipient added.");
  }

  const full = recipients.length >= limit;

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Email alerts for the front desk</h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-600">
          These addresses get an email when a visitor asks for a booking or a callback. The email
          names the event and links to the admin app. It never includes the visitor's name, phone,
          email, or message. Up to {limit} addresses.
        </p>
      </div>
      <ClientPicker scope={scope} id="notify-client" />
      {needsClient ? (
        <p className="text-sm text-slate-500">Choose a client to manage its alert recipients.</p>
      ) : loading ? (
        <p className="text-sm text-slate-500">Loading...</p>
      ) : (
        <div className="max-w-xl space-y-4">
          {recipients.length === 0 ? (
            <p className="text-sm text-slate-500">No one is on the list yet.</p>
          ) : (
            <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200">
              {recipients.map((item) => (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
                  <span className={`text-sm ${item.enabled ? "text-slate-900" : "text-slate-400 line-through"}`}>
                    {item.email}
                  </span>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-xs text-slate-600">
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        disabled={busy}
                        onChange={(event) =>
                          void run(
                            () => setNotificationRecipientEnabled(withClient, item.id, event.target.checked).then(() => undefined),
                            event.target.checked ? "Alerts turned on." : "Alerts turned off.",
                          )
                        }
                      />
                      Send alerts
                    </label>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(() => removeNotificationRecipient(withClient, item.id), "Recipient removed.")
                      }
                      className="text-xs text-red-700 underline disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={handleAdd} className="flex flex-wrap gap-2" noValidate>
            <label htmlFor="notify-email" className="sr-only">
              Email address to add
            </label>
            <input
              id="notify-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="front-desk@practice.com"
              disabled={busy || full}
              className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none ring-slate-400 focus:ring-2"
            />
            <button
              type="submit"
              disabled={busy || full || !email.trim()}
              className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Add recipient
            </button>
          </form>
          <p className="text-xs text-slate-500">
            {recipients.length} of {limit} used.
            {full ? " Remove one to add another." : ""}
          </p>

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
        </div>
      )}
    </section>
  );
}
