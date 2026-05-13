import clsx from "clsx";
import { forwardRef } from "react";
import type { InputHTMLAttributes } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

/**
 * Ring-inset (no border), subtle shadow, focus brings a 2-ring inset in
 * the brand accent. Background flips for dark mode; ring color flips for
 * invalid.
 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, invalid, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      {...rest}
      aria-invalid={invalid || undefined}
      className={clsx(
        // ring-inset (not border), shadow-sm, sized tight at sm. The input
        // bg matches the card bg in dark mode so the ring is what defines
        // the field's edge.
        "block w-full rounded-md border-0 px-3 py-1.5 shadow-sm",
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
  );
});
