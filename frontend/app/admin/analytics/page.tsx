import { AnalyticsDashboard } from "@/components/admin/analytics/analytics-dashboard";

export default function AdminAnalyticsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Analytics</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        How conversations end: bookings, callbacks, handovers, and questions the assistant could
        not answer. Test conversations are never counted.
      </p>
      <div className="mt-8">
        <AnalyticsDashboard />
      </div>
    </div>
  );
}
