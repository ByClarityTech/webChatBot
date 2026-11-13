"use client";

import { inputClass } from "@/components/auth/auth-card";

type PasswordFieldProps = {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  /** Whether the text is shown. The parent owns this state, one flag per field. */
  visible: boolean;
  onToggleVisible: () => void;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Shown under the input in the error colour. */
  error?: string | null;
};

/** Eye: the password is hidden, so the button offers to show it. */
function EyeIcon() {
  return (
    <svg
      className="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M1.5 12s3.8-7 10.5-7 10.5 7 10.5 7-3.8 7-10.5 7S1.5 12 1.5 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/** Eye with a slash: the password is showing, so the button offers to hide it. */
function EyeOffIcon() {
  return (
    <svg
      className="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M1.5 12s3.8-7 10.5-7 10.5 7 10.5 7-3.8 7-10.5 7S1.5 12 1.5 12z" />
      <circle cx="12" cy="12" r="3" />
      <path d="M3 3l18 18" />
    </svg>
  );
}

/** Controlled password input with a Show/Hide button inside its right edge. */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  visible,
  onToggleVisible,
  disabled,
  autoFocus,
  error,
}: PasswordFieldProps) {
  const errorId = `${id}-error`;

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-700">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={`${inputClass} pr-11 ${error ? "border-red-400" : ""}`}
        />
        <button
          type="button"
          onClick={onToggleVisible}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          disabled={disabled}
          className="pointer-events-auto absolute inset-y-0 right-0 z-10 flex items-center px-3 text-slate-500 transition hover:text-slate-700 focus:outline-none focus-visible:text-slate-900 disabled:opacity-50"
        >
          {visible ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      </div>
      {error && (
        <p id={errorId} className="mt-1.5 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
