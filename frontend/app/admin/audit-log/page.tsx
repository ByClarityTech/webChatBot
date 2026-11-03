import { AuditLogTable } from "@/components/admin/audit-log-table";

export default function AdminAuditLogPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Audit log</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Changes made in the admin, newest first: who did what, and when.
      </p>

      <div className="mt-8">
        <AuditLogTable />
      </div>
    </div>
  );
}
