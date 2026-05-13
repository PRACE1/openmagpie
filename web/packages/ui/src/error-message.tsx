import clsx from "clsx";

export interface ErrorMessageProps {
  message: string;
  className?: string;
}

export function ErrorMessage({ message, className }: ErrorMessageProps) {
  return (
    <div
      role="alert"
      className={clsx(
        "rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700",
        "dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300",
        className,
      )}
    >
      {message}
    </div>
  );
}
