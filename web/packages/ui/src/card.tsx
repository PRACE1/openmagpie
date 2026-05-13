import clsx from "clsx";
import type { HTMLAttributes } from "react";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {}

/**
 * Plain surface, flat, simple shadow, rounded-lg. No layered glass effects.
 */
export function Card({ className, ...rest }: CardProps) {
  return (
    <div
      {...rest}
      className={clsx(
        "rounded-lg bg-paper text-ink shadow",
        "dark:bg-ink-soft dark:text-paper",
        className,
      )}
    />
  );
}
