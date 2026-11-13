import { Suspense } from "react";

import { StaffInbox } from "@/components/admin/staff-inbox";

export default function InboxPage() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <h1 className="shrink-0 text-xl font-semibold text-slate-900">Staff inbox</h1>
      <Suspense fallback={null}>
        <StaffInbox />
      </Suspense>
    </div>
  );
}
