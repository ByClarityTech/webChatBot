import { BookingsTable } from "@/components/admin/bookings-table";

export default function AdminBookingsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Bookings</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        View appointments created via the chat widget or booking API. Cancel a booking to update
        its status and remove the Google Calendar event.
      </p>

      <div className="mt-8">
        <BookingsTable />
      </div>
    </div>
  );
}
