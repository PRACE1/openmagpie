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
_FEEDS = f"/{API_VERSION}/feeds"
_WATCHES = f"/{API_VERSION}/watches"
_ENGINES = f"/{API_VERSION}/engines"


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


class feeds:
    """`/v1/feeds/*` routes the CLI consumes."""

    collection = _FEEDS

    @staticmethod
    def detail(feed_id: str) -> str:
        return f"{_FEEDS}/{feed_id}"

    @staticmethod
    def sources(feed_id: str) -> str:
        return f"{_FEEDS}/{feed_id}/sources"

    @staticmethod
    def source_detail(feed_id: str, source_id: str) -> str:
        return f"{_FEEDS}/{feed_id}/sources/{source_id}"


class watches:
    """`/v1/watches/*` routes the CLI consumes."""

    collection = _WATCHES

    @staticmethod
    def detail(watch_id: str) -> str:
        return f"{_WATCHES}/{watch_id}"

    @staticmethod
    def actions(watch_id: str) -> str:
        return f"{_WATCHES}/{watch_id}/actions"

    @staticmethod
    def action_detail(watch_id: str, action_id: str) -> str:
        return f"{_WATCHES}/{watch_id}/actions/{action_id}"

    @staticmethod
    def action_runs(watch_id: str, action_id: str) -> str:
        return f"{_WATCHES}/{watch_id}/actions/{action_id}/runs"


class engines:
    """`/v1/engines/*` routes the CLI consumes."""

    collection = _ENGINES
