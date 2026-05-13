import clsx from "clsx";
import type { ReactNode } from "react";

export interface FormFieldProps {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  className?: string;
}

export function FormField({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
}: FormFieldProps) {
  return (
    <div className={clsx("space-y-1.5", className)}>
      <label
        htmlFor={htmlFor}
        className="block text-sm font-medium text-ink dark:text-paper"
      >
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-red-600 dark:text-red-400" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-ink-subtle dark:text-paper/60">{hint}</p>
      ) : null}
    </div>
  );
}
