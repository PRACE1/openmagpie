// Centralized route registry for the magpie web app.
//
// Two namespaces:
//   - `apiRoutes`, Django HTTP endpoints under settings.API_VERSION_PREFIX
//     that the BROWSER actually calls.
//   - `webRoutes`, Next.js frontend paths the browser programmatically
//     navigates to.
//
// Endpoints that exist on the server but only the CLI calls (token
// refresh / revoke, device-session create / poll) intentionally are
// NOT registered here, the CLI maintains its own routes registry in
// `cli/src/openmagpie/routes.py`.
//
// The API version prefix is read from NEXT_PUBLIC_API_VERSION at build /
// runtime, so a future `/v2` bump only needs the env change.

const VERSION = process.env.NEXT_PUBLIC_API_VERSION ?? "v1";

export const apiRoutes = {
  auth: {
    signup: `/${VERSION}/auth/signup`,
    login: `/${VERSION}/auth/login`,
    logout: `/${VERSION}/auth/logout`,
    me: `/${VERSION}/auth/me`,
    deviceSessionInfo: (sessionId: string) =>
      `/${VERSION}/auth/device-sessions/${sessionId}/info`,
    deviceSessionComplete: (sessionId: string) =>
      `/${VERSION}/auth/device-sessions/${sessionId}/complete`,
    deviceSessionDeny: (sessionId: string) =>
      `/${VERSION}/auth/device-sessions/${sessionId}/deny`,
  },
} as const;

export const webRoutes = {
  home: "/",
  signup: "/signup",
  login: "/login",
  logout: "/logout",
} as const;

/**
 * Append `?next=<path>` to a web route for the post-login redirect, used
 * by `useRequireAuth` and the signup/login forms to bounce users back to
 * where they were headed. Returns the path unchanged if `next` is empty.
 */
export function withNext(path: string, next: string | null | undefined): string {
  if (!next) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}next=${encodeURIComponent(next)}`;
}

/**
 * Validate a `?next=` value before navigating to it. Same-origin paths only:
 * must start with "/", must not start with "//" or "/\\" (both forms can
 * resolve to protocol-relative attacker URLs in some browsers), and must
 * not route the user straight into logout. Returns `fallback` for invalid
 * input.
 */
export function safeNext(
  next: string | null | undefined,
  fallback: string = webRoutes.home,
): string {
  if (!next) return fallback;
  if (!next.startsWith("/")) return fallback;
  if (next.startsWith("//")) return fallback;
  // Backslashes get normalized to "/" by some browser URL parsers, so
  // "/\\example.com" can land at "//example.com" (protocol-relative,
  // attacker-controlled host). Reject any path containing backslashes.
  if (next.includes("\\")) return fallback;
  // Redirecting a freshly-signed-in user straight to /logout is at best
  // a denial-of-UX, at worst a flow hijack. Treat as invalid `next`.
  if (
    next === webRoutes.logout ||
    next.startsWith(`${webRoutes.logout}?`) ||
    next.startsWith(`${webRoutes.logout}/`)
  ) {
    return fallback;
  }
  return next;
}
