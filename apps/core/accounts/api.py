"""Account-scope DRF mixin.

Single source of truth for "authenticated user → primary account id".
Every per-user API endpoint inherits from `AccountScopedAPIView` and
reads `request.account_id` directly. No inline AccountService lookups,
no per-view None guards, no three copies to keep in sync as the
scoping policy evolves (org-scoping, audit-on-lookup, etc.).
"""

from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from .services import AccountService

logger = logging.getLogger("accounts")


class NoPrimaryAccount(APIException):
    """An authenticated user has no primary account.

    Signup invariants create one; its absence is account corruption, not
    a normal empty state. The detail is a dict so DRF's default handler
    renders the body verbatim as `{"error": ..., "detail": ...}`,
    matching the legacy shape (no client branching on response shape).
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = {
        "error": "no_primary_account",
        "detail": "current user has no primary account",
    }
    default_code = "no_primary_account"


class AccountScopedAPIView(APIView):
    """APIView for endpoints scoped to the authenticated user's primary
    account. After `initial()` runs, `request.account_id: str` is the
    auth-derived account scope — verb methods construct services with
    `account_id=request.account_id` directly. The lookup happens once
    per request, in one place; views never call `primary_account_id_for`
    themselves.
    """

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        # super() runs authentication + permission_classes; by the time
        # control returns, `request.user.is_authenticated` is guaranteed
        # (IsAuthenticated is in our default permission_classes above).
        super().initial(request, *args, **kwargs)
        account_id = AccountService.Global.primary_account_id_for(user_id=str(request.user.id))
        if account_id is None:
            logger.error("user %s has no primary account", request.user.id)
            raise NoPrimaryAccount()
        request.account_id = account_id
