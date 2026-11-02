import { DocumentTable } from "@/components/admin/document-table";

export default function AdminDocumentsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Knowledge base</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Upload, list, reindex, and remove documents indexed for the public chat assistant.
      </p>

      <div className="mt-8">
        <DocumentTable />
      </div>
    </div>
  );
}
