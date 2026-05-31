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

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.fields import is_valid_ulid
from listeners.api import ListenerScopedAPIView
from listeners.registry import load_semantic_config
from openmagpie_schema.wire import HitListResponse, ListenerListResponse, NotifierPayload, PayloadSampleResponse

from .policy import PolicyError
from .serializers import (
    ListenerCreateSerializer,
    hit_wire,
    listener_mutation,
    listener_view,
    listener_wire,
)
from .services.hits import list_hits
from .services.listeners import ListenerService, SeedCursor
from .services.preview import CannotPreviewSource, build_preview
from .stats import compute_hit_rates

logger = logging.getLogger("listeners")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_HIT_LIMIT = 50
_MAX_HIT_LIMIT = 200


def _is_truthy(value: str | None) -> bool:
    """Parse a query-string flag. Absent or anything outside the truthy
    set reads as False, so `?dry_run=0` / no param creates for real."""
    return value is not None and value.strip().lower() in _TRUTHY


def _parse_hit_limit(request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_HIT_LIMIT
    try:
        return max(1, min(_MAX_HIT_LIMIT, int(raw)))
    except ValueError:
        return _DEFAULT_HIT_LIMIT


class ListenerListCreateView(AccountScopedAPIView):
    """POST  /v1/listeners,  create a new listener for the caller's account.
    GET   /v1/listeners,  list listeners in the caller's account.
    """

    def post(self, request):
        serializer = ListenerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        svc = ListenerService(account_id=request.account_id)

        # ?seed_cursor=<value>: validate against SeedCursor up-front so a
        # typo / unknown token (e.g. "lastest", "newest") 400s instead of
        # silently falling through to the empty-cursor default ; that
        # would re-judge the full retention window, the exact opposite of
        # the opt-out the operator just asked for. Validated even on
        # dry-run so the operator catches the typo before the real POST.
        seed_cursor_raw = request.query_params.get("seed_cursor") or None
        if seed_cursor_raw is not None:
            try:
                SeedCursor(seed_cursor_raw)
            except ValueError:
                raise ValidationError(
                    {"seed_cursor": [f"expected one of {[c.value for c in SeedCursor]}, got {seed_cursor_raw!r}"]}
                ) from None

        # build/create run the feed-exists policy check (the listener's
        # feed_id must reference a Feed in this account) -> PolicyError;
        # map it to a 400 like the serializer's shape/policy errors.
        try:
            if _is_truthy(request.query_params.get("dry_run")):
                # Validate-only: build the would-be record in memory and
                # return it WITHOUT persisting. `build()` runs the IDENTICAL
                # service validation as create, so the preview is faithful
                # for *validation* (not a create-success promise).
                preview = svc.build(
                    user_id=str(request.user.id),
                    name=d["name"],
                    instructions=d["instructions"],
                    kind=d["kind"],
                    delivery_mode=d["delivery_mode"],
                    data=d["data"],
                )
                preview_data = listener_mutation(preview, dry_run=True).model_dump(mode="json")
                # `id` is an empty-string placeholder pre-save; drop it so a
                # client never reads a meaningless id from the preview.
                preview_data.pop("id", None)
                return Response(preview_data, status=status.HTTP_200_OK)

            listener = svc.create(
                user_id=str(request.user.id),
                name=d["name"],
                instructions=d["instructions"],
                kind=d["kind"],
                delivery_mode=d["delivery_mode"],
                data=d["data"],
                seed_cursor=seed_cursor_raw,
            )
        except PolicyError as exc:
            return Response({"data": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        # Symmetric `dry_run: False` so a client can branch on the body
        # alone (the 201 vs 200 status already distinguishes them, this
        # just makes the contract explicit on both responses).
        return Response(
            listener_mutation(listener, dry_run=False).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        listeners = ListenerService(account_id=request.account_id).list()
        # Batched rolling hit rates (constant queries, no N+1).
        rates = compute_hit_rates(listeners)
        items = [listener_wire(o, recent=rates.get(str(o.id), (0, 0))) for o in listeners]
        return Response(ListenerListResponse(items=items).model_dump(mode="json"))


class ListenerDetailView(ListenerScopedAPIView):
    """GET / PUT / DELETE /v1/listeners/<id>, all account-scoped.

    PUT is full-replace edit and mirrors create's contract: same
    envelope validation, same `?dry_run=true` preview, same body shape
    (+ `summary`, `dry_run`). `kind` is immutable. Watermarks and `***`
    secrets carry forward (ListenerService.build_update); id / created_at
    / user_id / poll-state columns never change.
    """

    def get(self, request, listener_id: str):
        return Response(
            listener_view(self.listener).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def put(self, request, listener_id: str):
        listener = self.listener
        svc = self.listener_svc

        serializer = ListenerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if d["kind"] != listener.kind:
            # Immutable: a kind change swaps the config schema, making
            # watermark/secret preservation ill-defined. delete+recreate
            # is the path for switching kind.
            return Response(
                {
                    "error": "kind_immutable",
                    "detail": (f"listener kind is {listener.kind!r} and cannot be changed (requested {d['kind']!r})"),
                },
                status=status.HTTP_409_CONFLICT,
            )

        edit_kwargs = {
            "name": d["name"],
            "instructions": d["instructions"],
            "delivery_mode": d["delivery_mode"],
            "data": d["data"],
        }

        # Policy runs on the MERGED config inside build_update/update
        # (see services.build_update): engine/watermark/webhook guards,
        # plus the refusal to persist a masked secret that couldn't be
        # restored. Map all of them to a 400 with the same {"data": [...]}
        # shape the create path uses, so the CLI error printer is uniform.
        try:
            if _is_truthy(request.query_params.get("dry_run")):
                # Same validate-only contract as create's dry-run, but id
                # / created_at are real (existing row), so unlike create
                # we do NOT strip id - the preview shows the listener.
                preview = svc.build_update(listener, **edit_kwargs)
                return Response(
                    listener_mutation(preview, dry_run=True).model_dump(mode="json"),
                    status=status.HTTP_200_OK,
                )

            updated = svc.update(listener, **edit_kwargs)
            return Response(
                listener_mutation(updated, dry_run=False).model_dump(mode="json"),
                status=status.HTTP_200_OK,
            )
        except PolicyError as exc:
            return Response({"data": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, listener_id: str):
        self.listener_svc.delete(self.listener)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListenerRewindView(ListenerScopedAPIView):
    """POST /v1/listeners/<id>/rewind, an operator-issued cursor reset.

    Body: optional `{"to": "<ULID>"}` ; defaults to "" (re-judge the full
    retention window on next cycle). `to` may be any well-formed ULID
    (real FeedItem id, or one synthesized from a timestamp via
    `min_ulid_at`); it does NOT need to match an existing row. A
    malformed ULID returns 400 ; without this guard, a typo would
    silently brick the cursor (no item id__gt=typo matches -> listener
    appears dead).

    Cost: LLM tokens per re-judged item; the CLI confirms before sending.
    Returns the updated listener_view so the caller can confirm the new
    cursor.
    """

    def post(self, request, listener_id: str):
        # `to` may be null (or missing) to mean "reset to start of
        # retention", or a 26-char ULID string. Anything else (numbers,
        # bools, arrays, objects) is malformed and rejected ; the old
        # `str(raw) if raw else ""` form silently treated 0/false/[]/{}
        # as "reset", which on a destructive op (re-judges retention
        # window, burns LLM tokens) is the wrong default.
        raw = request.data.get("to") if isinstance(request.data, dict) else None
        if raw is None:
            to = ""
        elif isinstance(raw, str):
            to = raw
        else:
            raise ValidationError({"to": [f"must be a string or null, got {type(raw).__name__}"]})
        if to and not is_valid_ulid(to):
            raise ValidationError({"to": [f"expected a 26-char ULID or empty string, got {to!r}"]})
        self.listener_svc.rewind_judge_cursor(self.listener, to=to)
        return Response(
            listener_view(self.listener).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )


class NoObservationForSource(APIException):
    """409 raised when payload-sample can't honestly resolve an
    Observation class for the listener's feed source ; feed missing,
    feed config drifted out of schema, or none of the feed's source
    kinds have a registered connector in this deployment.

    HTTP-shaped translation of `CannotPreviewSource` from
    `services/preview.py`. Kept in the view layer so the service stays
    HTTP-agnostic."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "no_observation_for_source"
    default_detail = {
        "error": "no_observation_for_source",
        "detail": (
            "no registered Observation class matches this listener's feed source. "
            "Either the feed is missing/drifted, or the connector for any of the "
            "feed's source kinds isn't loaded in this deployment. The logs identify "
            "which case."
        ),
    }


class UnsupportedListenerKind(APIException):
    """422 raised when payload-sample is invoked on a listener whose
    kind isn't Semantic (only Semantic is supported today) or whose
    kind isn't registered at all (drift between a DB row and the
    current registry). Both surface as a structured error instead of
    a bare 500 ; payload-sample's whole point is to diagnose listener
    setup, so the diagnostic itself shouldn't crash on a broken row."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "unsupported_listener_kind"
    default_detail = {
        "error": "unsupported_listener_kind",
        "detail": "this listener's kind isn't supported by payload-sample (Semantic-only today, or kind not registered)",
    }


class ListenerPayloadSampleView(ListenerScopedAPIView):
    """GET /v1/listeners/<id>/payload-sample.

    Thin HTTP wrapper around `services.preview.build_preview` ; the
    dry-run delivery service does all the work (composes EventService,
    Observation registry, every configured notifier's render()). This
    view just translates domain exceptions to HTTP-shaped ones.
    """

    def get(self, request, listener_id: str):
        try:
            config = load_semantic_config(self.listener)
        except (NotImplementedError, KeyError) as exc:
            # load_semantic_config raises NotImplementedError on a
            # non-Semantic kind, and the underlying registry raises
            # KeyError on an unknown kind. Both = "this listener's kind
            # can't be previewed."
            raise UnsupportedListenerKind() from exc
        try:
            result = build_preview(self.listener, config, account_id=request.account_id)
        except CannotPreviewSource as exc:
            raise NoObservationForSource() from exc
        body = PayloadSampleResponse(
            synthetic=result.synthetic,
            notifiers=[NotifierPayload(kind=n.kind, target=n.target, rendered=n.rendered) for n in result.notifiers],
        )
        return Response(body.model_dump(mode="json"), status=status.HTTP_200_OK)


class ListenerHitsView(ListenerScopedAPIView):
    """GET /v1/listeners/<id>/hits, paginated by ULID pk, newest first.

    Cursor-pagination via `?after=<event_id>` (id strictly less than that).
    `?limit=N` caps page size (default 50, max 200). Account-scoped via
    `ListenerScopedAPIView` ; the inner query also filters by account_id
    for defense in depth.
    """

    def get(self, request, listener_id: str):
        limit = _parse_hit_limit(request)
        after = request.query_params.get("after") or None
        hits = list_hits(self.listener, after=after, limit=limit)
        next_cursor = str(hits[-1].id) if len(hits) == limit else None
        return Response(
            HitListResponse(items=[hit_wire(h) for h in hits], next_cursor=next_cursor).model_dump(mode="json")
        )
