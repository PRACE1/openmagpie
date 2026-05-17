"""HTTP entry points for /v1/listeners.

`ListenerListCreateView` handles POST (create) and GET (list). Both
gated on `IsAuthenticated`; account scoping happens via
`ListenerService(account_id=...)` keyed off the request user's primary
account.

Listener-kind-specific validation lives in the Pydantic registry
(`listeners.registry`); the serializer just delegates to it.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from accounts.services import AccountService
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ListenerCreateSerializer, ListenerSerializer
from .services.listeners import ListenerService

logger = logging.getLogger("listeners")

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    """Parse a query-string flag. Absent or anything outside the truthy
    set reads as False, so `?dry_run=0` / no param creates for real."""
    return value is not None and value.strip().lower() in _TRUTHY


def _serialize(listener) -> dict[str, Any]:
    """Serialize a Listener to its wire dict.

    The `cast` is load-bearing: `ListenerSerializer` has an output field
    literally named `data`, which makes the type checker read `.data` as
    that field rather than `BaseSerializer.data`. Centralized here so the
    rationale lives in one place, not at every call site.
    """
    return cast("dict[str, Any]", ListenerSerializer(listener).data)


class ListenerListCreateView(APIView):
    """POST  /v1/listeners,  create a new listener for the caller's account.
    GET   /v1/listeners,  list listeners in the caller's account.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ListenerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            # User has no primary account, signup invariants should
            # have created one. Surface as a server-side problem so
            # an operator can investigate rather than the client
            # quietly getting an empty 4xx.
            return Response(
                {
                    "error": "no_primary_account",
                    "detail": "current user has no primary account",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        svc = ListenerService(account_id=account_id)

        if _is_truthy(request.query_params.get("dry_run")):
            # Validate-only: build the would-be record in memory and
            # return it WITHOUT persisting. `build()` runs the IDENTICAL
            # serializer + service validation as create, so the preview
            # is faithful for *validation*. It does not guarantee save
            # success (persistence can still fail); the preview is a
            # validation preview, not a create-success promise.
            preview = svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                instructions=d["instructions"],
                kind=d["kind"],
                delivery_mode=d["delivery_mode"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            preview_data = _serialize(preview)
            # `id` is an empty-string placeholder pre-save; drop it so a
            # client never reads a meaningless id from the preview.
            preview_data.pop("id", None)
            return Response(
                {**preview_data, "dry_run": True},
                status=status.HTTP_200_OK,
            )

        listener = svc.create(
            user_id=str(request.user.id),
            name=d["name"],
            instructions=d["instructions"],
            kind=d["kind"],
            delivery_mode=d["delivery_mode"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
        )
        # Symmetric `dry_run: False` so a client can branch on the body
        # alone (the 201 vs 200 status already distinguishes them, this
        # just makes the contract explicit on both responses).
        return Response(
            {**_serialize(listener), "dry_run": False},
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            # Same invariant the POST path treats as a 500: every user
            # should have a primary account. GET can't usefully error
            # mid-list, but it must not silently mask the corruption -
            # log it so the broken path is observable either way.
            logger.error(
                "user %s has no primary account on listener list",
                request.user.id,
            )
            return Response({"items": []})
        listeners = ListenerService(account_id=account_id).list()
        return Response({"items": ListenerSerializer(listeners, many=True).data})
