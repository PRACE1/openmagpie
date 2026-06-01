"""Account-scoped APIView base.

Resolves the caller's primary account once per request and stashes it
as `request.account_id` so downstream views + services read a single
source of truth. The lookup is memoized on the request object.
"""

from __future__ import annotations

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView

from accounts.services import AccountService


class AccountScopedRequest(Request):
    """Typing view of the request inside an account-scoped view.

    `initial()` stashes `account_id` on the live DRF `Request` at runtime;
    declaring it here (a typing-only subclass, never instantiated) lets the
    checker see `request.account_id` as `str` instead of an unknown
    attribute. Feed/source-scoped views narrow further (see feeds.api)."""

    account_id: str


class AccountScopedAPIView(APIView):
    """Base for any endpoint that operates within the caller's account.

    Subclasses get `request.account_id` (str) for free in every handler
    after `initial()` resolves + caches it. The id is derived from the
    authenticated user's primary account; a user with no account is a
    403 (authn succeeded, but they can't act in any tenant).
    """

    permission_classes = [permissions.IsAuthenticated]
    # Typing-only narrowing: DRF assigns the real `Request` at runtime;
    # this tells the checker the stashed `account_id` is present.
    request: AccountScopedRequest

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        account_id = AccountService.Global.primary_account_id_for(user_id=str(request.user.id))
        if account_id is None:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(detail="No account for user")
        request.account_id = account_id
