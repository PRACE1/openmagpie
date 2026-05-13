"""Centralized API route paths used by the CLI.

Only paths the CLI itself calls live here, not the full server surface.
Browser-only endpoints (signup, login, logout) are intentionally absent:
the CLI's path to credentials is device-flow, and its "logout" goes
through `tokens.revoke`.

Grouped into class-namespaces per domain so call sites read naturally:
`routes.auth.me`, `routes.auth.tokens.refresh`,
`routes.auth.device_session(sid)`.
"""

from __future__ import annotations

API_VERSION = "v1"
_AUTH = f"/{API_VERSION}/auth"


class auth:
    """`/v1/auth/*` routes the CLI consumes."""

    base = _AUTH
    me = f"{_AUTH}/me"
    device_sessions = f"{_AUTH}/device-sessions"

    @staticmethod
    def device_session(session_id: str) -> str:
        return f"{_AUTH}/device-sessions/{session_id}"

    class tokens:
        """`/v1/auth/tokens/*`, bearer token lifecycle (refresh + revoke)."""

        refresh = f"{_AUTH}/tokens/refresh"
        revoke = f"{_AUTH}/tokens/revoke"
