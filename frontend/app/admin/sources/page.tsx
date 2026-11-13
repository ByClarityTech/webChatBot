import { SourcesManager } from "@/components/admin/sources/sources-manager";

export default function AdminSourcesPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Sources</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        The practice website is read once a month, and on request. Each change the crawl finds
        waits here for an admin. Only approved text reaches the chatbot.
      </p>

      <div className="mt-8">
        <SourcesManager />
      </div>
    </div>
  );
}
