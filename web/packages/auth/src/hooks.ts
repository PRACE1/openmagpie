"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ApiError, authActions, webRoutes, withNext } from "@magpie/api-utils";
import { useAuthStore } from "./store";

const CHECK = {
  IDLE: "idle",
  CHECKING: "checking",
  DONE: "done",
  ERROR: "error",
} as const;

type CheckState = (typeof CHECK)[keyof typeof CHECK];

/**
 * Fetch the current user via GET /v1/auth/me (cookie auth) and put them
 * in the store. Returns `{ user, loading, error }`:
 *
 *  - `loading=true` means: we haven't completed an auth check yet, so
 *    callers must NOT assume `user === null` implies "not logged in."
 *  - `loading=false, error=false`: the check completed. `user` is either
 *    populated (logged in) or `null` (server explicitly said 401, i.e.
 *    definitively not logged in).
 *  - `loading=false, error=true`: the check failed for a non-auth
 *    reason (network blip, CORS, server 5xx). `user` is left as-is;
 *    we don't know if they're logged in, so the consumer should NOT
 *    treat them as logged out. `useRequireAuth` declines to redirect
 *    in this state for the same reason.
 *
 * The check fires at most once per hook instance. Once it's `done` or
 * `error`, no retry. After login the form sets the store user directly
 * and we never round-trip /me.
 */
export function useUser() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  // `phase` is hook-local. `idle` until we decide whether to fetch; we
  // skip fetching entirely if the store already has a user (the
  // just-logged-in flow). A ref guards against duplicate fetches when
  // strict mode double-mounts the effect in dev.
  const [phase, setPhase] = useState<CheckState>(
    user !== null ? CHECK.DONE : CHECK.IDLE,
  );
  const startedRef = useRef(false);

  useEffect(() => {
    if (phase !== CHECK.IDLE) return;
    if (startedRef.current) return;
    startedRef.current = true;
    setPhase(CHECK.CHECKING);
    async function check() {
      try {
        const u = await authActions.me();
        setUser(u);
        setPhase(CHECK.DONE);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          // Server definitively said "no valid credential". Logged out.
          setUser(null);
          setPhase(CHECK.DONE);
        } else {
          // Network / CORS / 5xx / malformed body (ZodError). We don't
          // actually know whether the user is logged in. Leave `user`
          // alone (don't clobber any prior value) and surface `error`
          // so consumers can avoid taking destructive actions (like
          // bouncing to /login).
          setPhase(CHECK.ERROR);
        }
      }
    }
    check();
  }, [phase, setUser]);

  return {
    user,
    loading: phase === CHECK.IDLE || phase === CHECK.CHECKING,
    error: phase === CHECK.ERROR,
  };
}

/**
 * Redirect to /login if the current user is not authenticated. Use inside
 * page components that must be auth-gated (e.g. the device-authorize page).
 *
 * The current URL is preserved as `?next=...` so the login/signup forms
 * can route the user back after authenticating, critical for the CLI
 * device flow, which lands the browser at /auth/device/{id} and would
 * otherwise lose the session id on the way through /login.
 *
 * On network errors we DON'T redirect; a flaky connection shouldn't
 * forcibly log the user out. The consumer can read `error` to render
 * a retry / offline message if desired.
 */
export function useRequireAuth(redirectTo: string = webRoutes.login) {
  const { user, loading, error } = useUser();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (loading) return;
    if (error) return;
    if (user !== null) return;
    const qs = searchParams.toString();
    const here = qs ? `${pathname}?${qs}` : pathname;
    router.replace(withNext(redirectTo, here));
  }, [user, loading, error, router, redirectTo, pathname, searchParams]);

  return { user, loading, error };
}
