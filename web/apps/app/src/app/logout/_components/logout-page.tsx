"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, apiRoutes, webRoutes } from "@magpie/api-utils";
import { useAuthStore } from "@magpie/auth";

/**
 * Hit POST /v1/auth/logout to clear the `auth_token` cookie server-side,
 * clear the in-memory Zustand store, and bounce to /login. Used as a
 * dev escape hatch ("how do I reset auth without DevTools?") and as the
 * eventual destination for a user-visible "Sign out" button.
 */
export function LogoutPage() {
  const router = useRouter();
  const clear = useAuthStore((s) => s.clear);

  useEffect(() => {
    let cancelled = false;
    apiFetch(apiRoutes.auth.logout, { method: "POST" })
      .catch(() => {
        // Server unreachable / already logged out, local cleanup still
        // matters, so don't bail.
      })
      .finally(() => {
        // Local store cleanup runs unconditionally, if the user
        // navigated away mid-logout we still don't want a stale `user`
        // sitting in the Zustand store next time another component
        // reads it. Only the navigation is gated on `cancelled`, since
        // routing to /login when the user already left would be rude.
        clear();
        if (!cancelled) {
          router.replace(webRoutes.login);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [clear, router]);

  return (
    <div className="flex min-h-dvh items-center justify-center text-sm text-ink/60 dark:text-paper/60">
      Signing out…
    </div>
  );
}
