"""Resolve the user behind a request from EITHER a Bearer header OR the
`auth_token` cookie.

Both carry the same value, an OAuth Toolkit `AccessToken.token`. The
browser holds it in an HttpOnly cookie; the CLI sends it via
`Authorization: Bearer ...`. One lookup path, two delivery mechanisms.
"""

from accounts.models.user import User
from django.http import HttpRequest
from oauth2_provider.models import AccessToken

from .constants import AUTHORIZATION_META_KEY, BEARER_SCHEME
from .cookies import AUTH_COOKIE_NAME

_BEARER_PREFIX = f"{BEARER_SCHEME} "


def _user_from_access_token(token_value: str) -> User | None:
    try:
        access = AccessToken.objects.select_related("user").get(token=token_value)
    except AccessToken.DoesNotExist:
        return None
    if not access.is_valid():
        return None
    return access.user


def resolve_request_user(request: HttpRequest) -> User | None:
    """Return the authenticated User, or None."""
    token_value = extract_request_token(request)
    if token_value is None:
        return None
    return _user_from_access_token(token_value)


def extract_request_token(request: HttpRequest) -> str | None:
    """Return the raw access-token value from either the Bearer header or
    the `auth_token` cookie. Doesn't validate; just extracts. Used by
    logout (where we need the token to revoke it, not the user record).
    """
    auth_header = request.META.get(AUTHORIZATION_META_KEY, "")
    if auth_header.startswith(_BEARER_PREFIX):
        value = auth_header.removeprefix(_BEARER_PREFIX).strip()
        if value:
            return value

    cookie_value = request.COOKIES.get(AUTH_COOKIE_NAME)
    return cookie_value or None
