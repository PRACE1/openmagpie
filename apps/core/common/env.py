"""Tiny env-parsing helpers.

Settings boolean reads like
    AUTH_COOKIE_SECURE = os.environ.get("X", "false").lower() == "true"
are whitespace-fragile, a trailing newline or leading space from a
secrets manager flips the meaning silently. `env_bool` strips before
comparing.
"""

from __future__ import annotations

import os


def env_bool(name: str, default: str = "false") -> bool:
    """Return True iff the env var (after strip + casefold) equals 'true'.

    Anything else (including unset → falls back to `default`, then
    'false' / '0' / 'no' / empty) is False. Whitespace and case
    differences don't change the result.
    """
    return os.environ.get(name, default).strip().lower() == "true"
