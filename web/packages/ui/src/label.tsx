import clsx from "clsx";
import type { LabelHTMLAttributes } from "react";

export type LabelProps = LabelHTMLAttributes<HTMLLabelElement>;

export function Label({ className, ...rest }: LabelProps) {
  return (
    <label
      {...rest}
      className={clsx(
        "block text-sm leading-6 font-medium text-ink dark:text-paper",
        className,
      )}
    />
  );
}
