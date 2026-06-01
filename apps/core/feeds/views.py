"""HTTP entry points for /v1/feeds.

`FeedListCreateView` handles POST (create) + GET (list). `FeedDetailView`
handles GET / PUT / DELETE on `/v1/feeds/<id>`; its GET is the "sort
by new and go" reader (returns the feed + its recent items, with
optional ?limit). `FeedSourcesView` + `FeedSourceDetailView` cover the
`/sources` sub-router.

The `/v1/feeds/<id>/...` views inherit `FeedScopedAPIView` and read
`self.feed` directly ; a missing feed raises `FeedNotFound`, DRF
converts to 404, no manual response juggling inside handlers.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.pydantic_errors import pydantic_errors_to_drf
from openmagpie_schema.feed import FeedListResponse

from .api import FeedItemSvcMixin, FeedScopedAPIView, FeedSvcMixin, SourceScopedAPIView, SourceSvcMixin
from .policy import PolicyError
from .serializers import (
    SOURCE_INPUT_LIST_ADAPTER,
    FeedCreateSerializer,
    feed_mutation,
    feed_view,
    feed_wire,
    source_wire,
)
from .services.sources import ConcurrentSetSourcesError

logger = logging.getLogger("feeds")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_ITEM_LIMIT = 50
_MAX_ITEM_LIMIT = 200


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _parse_limit(request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_ITEM_LIMIT
    try:
        return max(1, min(_MAX_ITEM_LIMIT, int(raw)))
    except ValueError:
        return _DEFAULT_ITEM_LIMIT


class FeedListCreateView(FeedSvcMixin, AccountScopedAPIView):
    """POST /v1/feeds (create), GET /v1/feeds (list)."""

    def post(self, request):
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if _is_truthy(request.query_params.get("dry_run")):
            preview = self.feed_svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                kind=d["kind"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            preview_data = feed_mutation(preview, dry_run=True).model_dump(mode="json")
            preview_data.pop("id", None)  # empty placeholder pre-save
            return Response(preview_data, status=status.HTTP_200_OK)

        feed = self.feed_svc.create(
            user_id=str(request.user.id),
            name=d["name"],
            kind=d["kind"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
            sources=d.get("sources") or None,
        )
        return Response(
            feed_mutation(feed, dry_run=False).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        limit = _parse_limit(request)
        after = request.query_params.get("after") or None
        feeds = self.feed_svc.list(after=after, limit=limit)
        # Page is "full" iff we got `limit` rows; if so, more pages may exist
        # and the last row's id is the cursor for the next page.
        next_cursor = str(feeds[-1].id) if len(feeds) == limit else None
        return Response(
            FeedListResponse(items=[feed_wire(o) for o in feeds], next_cursor=next_cursor).model_dump(mode="json")
        )


class FeedDetailView(FeedItemSvcMixin, FeedScopedAPIView):
    """GET / PUT / DELETE /v1/feeds/<id>, all account-scoped. GET is the
    'sort by new and go' reader (feed + recent items, ?limit)."""

    def get(self, request, feed_id: str):
        items = self.feed_item_svc.list_recent_items(self.feed, limit=_parse_limit(request))
        return Response(
            feed_view(self.feed, recent_items=items).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def put(self, request, feed_id: str):
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if d["kind"] != self.feed.kind:
            return Response(
                {
                    "error": "kind_immutable",
                    "detail": f"feed kind is {self.feed.kind!r} and cannot be changed (requested {d['kind']!r})",
                },
                status=status.HTTP_409_CONFLICT,
            )

        edit_kwargs = {
            "name": d["name"],
            "poll_interval_seconds": d["poll_interval_seconds"],
            "data": d["data"],
        }
        # Policy runs on the merged config inside build_update/update;
        # map PolicyError -> 400 (same shape create uses).
        try:
            if _is_truthy(request.query_params.get("dry_run")):
                preview = self.feed_svc.build_update(self.feed, **edit_kwargs)
                return Response(
                    feed_mutation(preview, dry_run=True).model_dump(mode="json"),
                    status=status.HTTP_200_OK,
                )
            updated = self.feed_svc.update(self.feed, **edit_kwargs)
            return Response(
                feed_mutation(updated, dry_run=False).model_dump(mode="json"),
                status=status.HTTP_200_OK,
            )
        except PolicyError as exc:
            return Response({"data": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, feed_id: str):
        self.feed_svc.delete(self.feed)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FeedSourcesView(SourceSvcMixin, FeedScopedAPIView):
    """Sources sub-router on `/v1/feeds/<id>/sources`.

      GET  list the feed's sources
      PUT  set/replace the whole list (body = {sources: [SourceInput], dry_run?})

    Per-row DELETE lives on `FeedSourceDetailView` so the URL keys the
    target row directly. Single-row add is intentionally absent ; the
    create-time path is the inline `sources:` block on `feed create`,
    and ongoing mutation is `export-sources -> edit -> set-sources`."""

    def get(self, request, feed_id: str):
        return Response(
            {"items": [source_wire(s).model_dump(mode="json") for s in self.source_svc.list(self.feed)]},
            status=status.HTTP_200_OK,
        )

    def put(self, request, feed_id: str):
        # DRF happily parses a bare top-level JSON array (CLI's own
        # `_parse_set_payload` accepts that shape, so a hand-rolled
        # client easily sends it). `.get(...)` on a list raises
        # AttributeError -> 500. Reject up front with a 400 naming the
        # required shape.
        body = request.data
        if not isinstance(body, dict):
            return Response(
                {"detail": "request body must be a JSON object with a `sources` array"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            items = SOURCE_INPUT_LIST_ADAPTER.validate_python(body.get("sources") or [])
        except PydanticValidationError as exc:
            return Response({"sources": pydantic_errors_to_drf(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # `dry_run` is a JSON bool. Reject anything else: `bool("false")`
        # is True (non-empty string), so a hand-rolled client sending
        # the string `"false"` would silently flip to dry-run and the
        # operator would see "would: ..." instead of the real apply.
        # Accept only a real bool (or absent, defaults to False).
        raw_dry_run = body.get("dry_run", False)
        if not isinstance(raw_dry_run, bool):
            return Response(
                {"detail": "`dry_run` must be a JSON boolean (true or false)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dry_run = raw_dry_run
        try:
            result = self.source_svc.set_sources(self.feed, items, dry_run=dry_run)
        except PolicyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ConcurrentSetSourcesError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(result.model_dump(mode="json"), status=status.HTTP_200_OK)


class FeedSourceDetailView(SourceScopedAPIView):
    """Per-row source ops on `/v1/feeds/<id>/sources/<source_id>`.

    Scoped to the feed in the path so a `source_id` from another feed
    (or another account) gives a 404 via `SourceNotFound`, not a 204
    on the wrong row."""

    def delete(self, request, feed_id: str, source_id: str):
        try:
            self.source_svc.remove(self.feed, source_id=self.source.id)
        except PolicyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)
