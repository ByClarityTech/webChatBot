import { AllowedDomainsForm } from "@/components/admin/allowed-domains-form";
import { BusinessHoursForm } from "@/components/admin/business-hours-form";
import { NotificationRecipientsForm } from "@/components/admin/notification-recipients-form";
import { PracticeContactForm } from "@/components/admin/practice-contact-form";

export default function AdminSettingsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Business hours</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Configure weekly availability, slot length, buffer time, and timezone. Changes apply to
        the public booking availability API.
      </p>

      <div className="mt-8 rounded-xl border border-slate-200 bg-white p-6">
        <BusinessHoursForm />
      </div>

      <div className="mt-8 rounded-xl border border-slate-200 bg-white p-6">
        <PracticeContactForm />
      </div>

      <div className="mt-8 rounded-xl border border-slate-200 bg-white p-6">
        <NotificationRecipientsForm />
      </div>

      {/* Renders nothing for Support users, who cannot change the allowed sites. */}
      <div className="mt-8">
        <AllowedDomainsForm />
      </div>
    </div>
  );
}
