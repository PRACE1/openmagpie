"""HTTP entry points for /v1/listeners.

`ListenerListCreateView` handles POST (create) and GET (list). Both
gated on `IsAuthenticated`; account scoping happens via
`ListenerService(account_id=...)` keyed off the request user's primary
account.

Listener-kind-specific validation lives in the Pydantic registry
(`listeners.registry`); the serializer just delegates to it.
"""

from __future__ import annotations

from typing import Any, cast

from accounts.services import AccountService
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ListenerCreateSerializer, ListenerSerializer
from .services.listeners import ListenerService

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    """Parse a query-string flag. Absent or anything outside the truthy
    set reads as False, so `?dry_run=0` / no param creates for real."""
    return value is not None and value.strip().lower() in _TRUTHY


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
            # Validate-only: build the would-be record in memory (same
            # serializer + service validation as the real create, so the
            # preview can't drift) and return it WITHOUT persisting.
            preview = svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                instructions=d["instructions"],
                kind=d["kind"],
                delivery_mode=d["delivery_mode"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            # cast: the serializer's output field is literally named
            # `data`, which confuses the type checker into reading
            # `.data` as that field rather than BaseSerializer.data.
            preview_data = cast("dict[str, Any]", ListenerSerializer(preview).data)
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
        created_data = cast("dict[str, Any]", ListenerSerializer(listener).data)
        # Symmetric `dry_run: False` so a client can branch on the body
        # alone (the 201 vs 200 status already distinguishes them, this
        # just makes the contract explicit on both responses).
        return Response(
            {**created_data, "dry_run": False},
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            return Response({"items": []})
        listeners = list(ListenerService(account_id=account_id).list())
        return Response({"items": ListenerSerializer(listeners, many=True).data})
