"""Listener-scope DRF mixin.

For endpoints under `/v1/listeners/<listener_id>/...`. Ids land on the
request (`request.account_id` from the parent mixin, `request.listener_id`
from the URL pattern); the resolved service and row are cached_properties
on the view (`self.listener_svc`, `self.listener`) so they're loaded once
per request, lazily, and not muddled into the request namespace.
"""

from __future__ import annotations

from functools import cached_property

from rest_framework import status
from rest_framework.exceptions import APIException

from accounts.api import AccountScopedAPIView

from .models import Listener
from .services.listeners import ListenerService


class ListenerNotFound(APIException):
    """404 for a listener absent from the caller's account. Same body
    whether it never existed or belongs to another account — not
    distinguishing IS the account-scoping guarantee."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, listener_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no listener {listener_id}"},
            code=self.default_code,
        )


class ListenerScopedAPIView(AccountScopedAPIView):
    """APIView for endpoints scoped to one listener within the
    authenticated user's account.

    After `initial()` runs:
      - `request.account_id: str` — from AccountScopedAPIView
      - `request.listener_id: str` — from the URL `<str:listener_id>` capture

    Verb methods access the resolved objects via:
      - `self.listener_svc: ListenerService` — account-scoped
      - `self.listener: Listener` — the resolved row, raises
        ListenerNotFound on first access if the row is absent

    Both are `cached_property`, so a verb method that touches
    `self.listener` twice incurs one DB hit, and a verb method that
    never touches it skips the lookup entirely.
    """

    def initial(self, request, *args, **kwargs):
        # super() runs auth + account scope; `request.account_id` is
        # populated by the time control returns.
        super().initial(request, *args, **kwargs)
        request.listener_id = kwargs.get("listener_id")

    @cached_property
    def listener_svc(self) -> ListenerService:
        return ListenerService(account_id=self.request.account_id)

    @cached_property
    def listener(self) -> Listener:
        try:
            return self.listener_svc.get(self.request.listener_id)
        except Listener.DoesNotExist as exc:
            raise ListenerNotFound(self.request.listener_id) from exc
