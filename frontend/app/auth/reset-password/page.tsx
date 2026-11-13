import { Suspense } from "react";

import { PasswordLinkForm } from "@/components/auth/set-password-form";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <PasswordLinkForm mode="reset" />
    </Suspense>
  );
}
