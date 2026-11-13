"use client";

// Shown under an urgent safety reply. The call buttons are real links, so a phone opens the
// dialer. The text is bilingual, like the reply above it, so a wrong guess about the visitor's
// language does not hide the number to call.

/** tel: link for the practice's number, or null when the stored value has no digits. */
export function officeDialHref(phone: string | null | undefined): string | null {
  const digits = (phone ?? "").replace(/[^\d+]/g, "");
  return digits.replace(/\+/g, "").length > 0 ? `tel:${digits}` : null;
}

// White on dark red and dark red on white both clear WCAG AA (4.5:1) with room to spare.
const buttonClass =
  "inline-flex min-h-[44px] flex-1 flex-col items-center justify-center rounded-xl bg-white px-4 py-2 text-center text-sm font-bold text-red-900 shadow-sm transition hover:bg-red-50 focus:outline-none focus-visible:ring-4 focus-visible:ring-white";

export function EmergencyAlertCard({ phone }: { phone: string | null }) {
  const officeHref = officeDialHref(phone);
  return (
    <section
      role="alert"
      aria-labelledby="emergency-alert-title"
      className="mt-2 max-w-[85%] rounded-2xl border-2 border-red-900 bg-red-700 p-4 text-white"
    >
      <h3 id="emergency-alert-title" className="text-base font-bold">
        Emergency? Call 911 now.
      </h3>
      <p className="mt-1 text-sm">¿Es una emergencia? Llame al 911 ahora.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <a href="tel:911" className={buttonClass}>
          <span>Call 911</span>
          <span className="font-normal">Llame al 911</span>
        </a>
        {officeHref && (
          <a href={officeHref} className={buttonClass}>
            <span>Call our office {phone}</span>
            <span className="font-normal">Llame a la oficina {phone}</span>
          </a>
        )}
      </div>
    </section>
  );
}
