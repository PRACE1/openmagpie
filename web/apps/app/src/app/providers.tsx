"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

/**
 * System / light / dark via next-themes. The `dark` class is added to <html>
 * dynamically when needed; light is the default class-free state.
 *
 * `disableTransitionOnChange` kills the brief color flash that otherwise
 * happens when the theme flips on a `transition-colors`-laden surface.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </ThemeProvider>
  );
}
