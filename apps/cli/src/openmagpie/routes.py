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
_LISTENERS = f"/{API_VERSION}/listeners"
_FEEDS = f"/{API_VERSION}/feeds"
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


class listeners:
    """`/v1/listeners/*` routes the CLI consumes."""

    collection = _LISTENERS

    @staticmethod
    def detail(listener_id: str) -> str:
        return f"{_LISTENERS}/{listener_id}"

    @staticmethod
    def rewind(listener_id: str) -> str:
        return f"{_LISTENERS}/{listener_id}/rewind"

    @staticmethod
    def payload_sample(listener_id: str) -> str:
        return f"{_LISTENERS}/{listener_id}/payload-sample"

    @staticmethod
    def hits(listener_id: str) -> str:
        return f"{_LISTENERS}/{listener_id}/hits"


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


class engines:
    """`/v1/engines/*` routes the CLI consumes."""

    collection = _ENGINES
