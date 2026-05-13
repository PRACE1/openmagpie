"""Custom DRF exception handler.

Wraps DRF's default handler with one extra behavior: when a request
that carries the `auth_token` cookie fails authentication, attach a
clearing `Set-Cookie` so the browser stops sending the dead credential
on every subsequent request. Without this, a revoked/expired cookie
keeps producing 401s until the user manually logs out, even though the
server already knows the credential is invalid.

Bearer-only failures don't trigger the clear: the CLI manages its own
on-disk token via `MagpieClient` and clears local creds in its own
401-on-refresh path.
"""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.views import exception_handler as drf_exception_handler

from .cookies import AUTH_COOKIE_NAME, delete_auth_cookie


def auth_aware_exception_handler(exc: Exception, context: dict[str, Any]):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        request = context.get("request")
        if request is not None and AUTH_COOKIE_NAME in request.COOKIES:
            delete_auth_cookie(response)
    return response
