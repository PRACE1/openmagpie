/**
 * Tiny fetch wrapper with consistent error handling + credential semantics.
 *
 * - `credentials: "include"` so the auth_token cookie is sent on every API
 *   call. CLI usage routes through a different client entirely
 *   (cli/src/openmagpie/http.py uses Bearer).
 * - JSON in, JSON out. Throws `ApiError` (carrying status + parsed body)
 *   on non-2xx so callers can `try/catch` and surface form-level errors.
 * - For trusted shapes that drive auth state, use `apiFetchParsed`; it
 *   runs a runtime schema check so a malformed response throws instead
 *   of silently flowing into the store as a bad cast.
 */

import type { ZodType } from "zod";

function resolveApiBase(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  if (fromEnv) return fromEnv;
  if (process.env.NODE_ENV === "production") {
    // Production builds with no API URL almost certainly mean the env
    // wasn't wired up. Failing loudly at module load is better than
    // silently shipping a build that hits localhost from the user's
    // browser.
    throw new Error(
      "NEXT_PUBLIC_API_URL is required in production. " +
        "Set it at build time to the API origin (e.g. https://api.example.com).",
    );
  }
  // Dev fallback so a fresh `pnpm dev` works zero-config.
  return "http://localhost:8000";
}

const API_BASE = resolveApiBase();

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options;
  const init: RequestInit = {
    credentials: "include",
    ...rest,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(headers as Record<string, string> | undefined),
    },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const response = await fetch(url, init);

  const text = await response.text();
  const parsed = text ? safeParseJson(text) : null;

  if (!response.ok) {
    const message =
      (parsed && typeof parsed === "object" && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : null) ?? `Request failed with status ${response.status}`;
    throw new ApiError(response.status, parsed, message);
  }

  return parsed as T;
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * Like `apiFetch`, but validates the response against a Zod schema and
 * returns the parsed value. Throws `ZodError` on mismatch; caller can
 * treat that as "server returned an unexpected shape" and surface a
 * generic failure rather than trusting the body.
 *
 * Use this for auth-shaped responses (`/me`, signup, login,
 * device-session info) where a bad shape silently flowing into the auth
 * store would be worse than an outright failure.
 */
export async function apiFetchParsed<T>(
  schema: ZodType<T>,
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const raw = await apiFetch<unknown>(path, options);
  return schema.parse(raw);
}
