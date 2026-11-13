import { ConversationsView } from "@/components/admin/conversations/conversations-view";

export default function AdminConversationsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">Conversations</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Each conversation with its outcome. Opening a transcript is recorded in the audit log.
      </p>
      <div className="mt-8">
        <ConversationsView />
      </div>
    </div>
  );
}
