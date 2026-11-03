import { ClientsTable } from "@/components/admin/clients-table";

export default function AdminClientsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Clients</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Each client is one business using the assistant. Suspending a client signs out all of
        its users and blocks their sign-in until it is activated again.
      </p>

      <div className="mt-8">
        <ClientsTable />
      </div>
    </div>
  );
}
