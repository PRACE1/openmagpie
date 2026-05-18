"""Frontend route templates the server needs to build absolute URLs to.

These mirror `web/packages/api-utils/src/routes.ts:webRoutes`, same
wire contract, two sides. Django can't reverse() these because the
frontend pages live in Next.js, not Django's URLconf, so we keep one
named constant per frontend path and centralize URL assembly in
`web_url(...)`.

When a frontend page path changes, update both the TypeScript
registry and this file.
"""

from __future__ import annotations

from typing import Final

from django.conf import settings

# Path templates. `{session_id}` style placeholders are filled by
# `web_url`'s str.format kwargs.
AUTH_DEVICE: Final[str] = "/auth/device/{session_id}"


def web_url(path: str, /, **params: object) -> str:
    """Build an absolute URL into the frontend.

    `path` is one of the templates above; `params` fills any placeholders.
    Joins against `settings.WEB_BASE_URL`, trimming the trailing slash so
    we never double up.

        >>> web_url(AUTH_DEVICE, session_id="abc123")
        "http://localhost:3001/auth/device/abc123"
    """
    base = settings.WEB_BASE_URL.rstrip("/")
    return f"{base}{path.format(**params)}"
