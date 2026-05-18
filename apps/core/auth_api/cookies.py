"""Cookie-based auth.

The access_token value is stored in an `auth_token` HttpOnly cookie, set
on signup / login / refresh responses and read back by
`auth_backends.resolve_request_user`.

We use the OAuth Toolkit access token as the cookie body so:
  - Browser sessions and CLI Bearer tokens share the same revocation model.
  - Refresh / expiry semantics are identical across surfaces.
"""

from django.conf import settings
from django.http import HttpResponse

AUTH_COOKIE_NAME = "auth_token"
AUTH_COOKIE_PATH = "/"
AUTH_COOKIE_SAMESITE = "Lax"


def _cookie_kwargs() -> dict:
    domain = getattr(settings, "AUTH_COOKIE_DOMAIN", None) or None
    return {
        "httponly": True,
        "samesite": AUTH_COOKIE_SAMESITE,
        "secure": getattr(settings, "AUTH_COOKIE_SECURE", True),
        "path": AUTH_COOKIE_PATH,
        "domain": domain,
    }


def set_auth_cookie(response: HttpResponse, token_value: str, max_age: int) -> HttpResponse:
    """Attach `auth_token=<token>` to the response with the configured TTL."""
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token_value,
        max_age=max_age,
        **_cookie_kwargs(),
    )
    return response


def delete_auth_cookie(response: HttpResponse) -> HttpResponse:
    # Domain must match set_auth_cookie or the browser keeps the old
    # cookie around: delete only takes effect when (name, path, domain)
    # all match the original.
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        domain=getattr(settings, "AUTH_COOKIE_DOMAIN", None) or None,
        samesite=AUTH_COOKIE_SAMESITE,
    )
    return response
