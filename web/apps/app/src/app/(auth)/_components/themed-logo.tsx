import { Logo } from "@magpie/ui";

/**
 * Renders the wordmark in both light and dark variants and lets Tailwind's
 * `dark:` variant pick which one is visible. Pure CSS, no theme detection
 * needed, so no hydration mismatch.
 */
export function ThemedLogo({ height = 52 }: { height?: number }) {
  return (
    <>
      <Logo height={height} on="light" className="block dark:hidden" />
      <Logo height={height} on="dark" className="hidden dark:block" />
    </>
  );
}
