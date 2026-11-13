import { Suspense } from "react";

import { PasswordLinkForm } from "@/components/auth/set-password-form";

export default function SetupPasswordPage() {
  return (
    <Suspense fallback={null}>
      <PasswordLinkForm mode="setup" />
    </Suspense>
  );
}
