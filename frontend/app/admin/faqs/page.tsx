import { FaqGapsPanel } from "@/components/admin/faq-gaps-panel";
import { FaqManager } from "@/components/admin/faq-manager";

export default function AdminFaqsPage() {
  return (
    <div className="px-8 py-10">
      <h1 className="text-2xl font-semibold text-slate-900">FAQs</h1>
      <p className="mt-2 max-w-2xl text-sm text-slate-600">
        Questions and answers the chat assistant uses first. Generate drafts from your documents
        or add your own; only approved FAQs reach the chatbot. Up to four approved FAQs can be
        default questions, shown when a visitor opens the chat.
      </p>

      <div className="mt-8">
        <FaqGapsPanel />
      </div>

      <div className="mt-8">
        <FaqManager />
      </div>
    </div>
  );
}
