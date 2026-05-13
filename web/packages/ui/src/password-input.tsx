"use client";

import clsx from "clsx";
import { forwardRef, useState } from "react";
import type { InputHTMLAttributes } from "react";

export interface PasswordInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  invalid?: boolean;
}

/**
 * Password field with a show/hide eye toggle. Same styling envelope as
 * `Input` (ring-inset, shadow-sm) so it slots in where Input would.
 *
 * The toggle button is positioned absolutely inside the input's box; we
 * pad the input on the right to leave room for it.
 */
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput({ className, invalid, ...rest }, ref) {
    const [revealed, setRevealed] = useState(false);
    return (
      <div className="relative">
        <input
          ref={ref}
          {...rest}
          type={revealed ? "text" : "password"}
          aria-invalid={invalid || undefined}
          className={clsx(
            "block w-full rounded-md border-0 px-3 py-1.5 pr-10 shadow-sm",
            "bg-paper text-ink ring-1 ring-inset ring-ink/15",
            "placeholder:text-ink/40",
            "focus:outline-none focus:ring-2 focus:ring-inset focus:ring-signal",
            "disabled:opacity-50 disabled:cursor-not-allowed",
            "sm:text-sm sm:leading-6",
            "dark:bg-ink-soft dark:text-paper dark:ring-paper/20 dark:placeholder:text-paper/40",
            invalid && "ring-red-500 focus:ring-red-500 dark:ring-red-500",
            className,
          )}
        />
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          aria-label={revealed ? "Hide password" : "Show password"}
          aria-pressed={revealed}
          className={clsx(
            "absolute inset-y-0 right-0 flex items-center px-2.5",
            "text-ink/50 hover:text-ink dark:text-paper/50 dark:hover:text-paper",
            "focus:outline-none focus-visible:text-signal",
          )}
        >
          {revealed ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      </div>
    );
  },
);

function EyeIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-4"
    >
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-4"
    >
      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
      <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
      <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
      <line x1="2" y1="2" x2="22" y2="22" />
    </svg>
  );
}
