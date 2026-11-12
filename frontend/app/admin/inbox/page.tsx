import { Suspense } from "react";

import { StaffInbox } from "@/components/admin/staff-inbox";

export default function InboxPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">Staff inbox</h1>
      <Suspense fallback={null}>
        <StaffInbox />
      </Suspense>
    </div>
  );
}
