"""HTTP entry points for /v1/feeds.

`FeedListCreateView` handles POST (create) + GET (list). `FeedDetailView`
handles GET / PUT / DELETE; its GET is the "sort by new and go" reader
(returns the feed + its recent items, with optional ?limit).
Mirrors `listeners.views`.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from openmagpie_schema.feed import FeedListResponse

from .models import Feed
from .policy import PolicyError
from .serializers import (
    FeedCreateSerializer,
    feed_mutation,
    feed_view,
    feed_wire,
)
from .services.feeds import FeedService

logger = logging.getLogger("feeds")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_ITEM_LIMIT = 50
_MAX_ITEM_LIMIT = 200


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _not_found_response(feed_id: str) -> Response:
    return Response(
        {"error": "not_found", "detail": f"no feed {feed_id}"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _parse_limit(request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return _DEFAULT_ITEM_LIMIT
    try:
        return max(1, min(_MAX_ITEM_LIMIT, int(raw)))
    except ValueError:
        return _DEFAULT_ITEM_LIMIT


class FeedListCreateView(AccountScopedAPIView):
    """POST /v1/feeds (create), GET /v1/feeds (list)."""

    def post(self, request):
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        svc = FeedService(account_id=request.account_id)

        if _is_truthy(request.query_params.get("dry_run")):
            preview = svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                kind=d["kind"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            preview_data = feed_mutation(preview, dry_run=True).model_dump(mode="json")
            preview_data.pop("id", None)  # empty placeholder pre-save
            return Response(preview_data, status=status.HTTP_200_OK)

        feed = svc.create(
            user_id=str(request.user.id),
            name=d["name"],
            kind=d["kind"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
        )
        return Response(
            feed_mutation(feed, dry_run=False).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        limit = _parse_limit(request)
        after = request.query_params.get("after") or None
        feeds = FeedService(account_id=request.account_id).list(after=after, limit=limit)
        # Page is "full" iff we got `limit` rows; if so, more pages may exist
        # and the last row's id is the cursor for the next page.
        next_cursor = str(feeds[-1].id) if len(feeds) == limit else None
        return Response(
            FeedListResponse(items=[feed_wire(o) for o in feeds], next_cursor=next_cursor).model_dump(mode="json")
        )


class FeedDetailView(AccountScopedAPIView):
    """GET / PUT / DELETE /v1/feeds/<id>, all account-scoped. GET is the
    'sort by new and go' reader (feed + recent items, ?limit)."""

    def _resolve(self, request, feed_id: str):
        svc = FeedService(account_id=request.account_id)
        try:
            return svc, svc.get(feed_id)
        except Feed.DoesNotExist:
            return _not_found_response(feed_id)

    def get(self, request, feed_id: str):
        resolved = self._resolve(request, feed_id)
        if isinstance(resolved, Response):
            return resolved
        svc, feed = resolved
        items = svc.list_recent_items(feed, limit=_parse_limit(request))
        return Response(
            feed_view(feed, recent_items=items).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def put(self, request, feed_id: str):
        resolved = self._resolve(request, feed_id)
        if isinstance(resolved, Response):
            return resolved
        svc, feed = resolved

        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if d["kind"] != feed.kind:
            return Response(
                {
                    "error": "kind_immutable",
                    "detail": f"feed kind is {feed.kind!r} and cannot be changed (requested {d['kind']!r})",
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
                preview = svc.build_update(feed, **edit_kwargs)
                return Response(
                    feed_mutation(preview, dry_run=True).model_dump(mode="json"),
                    status=status.HTTP_200_OK,
                )
            updated = svc.update(feed, **edit_kwargs)
            return Response(
                feed_mutation(updated, dry_run=False).model_dump(mode="json"),
                status=status.HTTP_200_OK,
            )
        except PolicyError as exc:
            return Response({"data": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, feed_id: str):
        resolved = self._resolve(request, feed_id)
        if isinstance(resolved, Response):
            return resolved
        svc, feed = resolved
        svc.delete(feed)
        return Response(status=status.HTTP_204_NO_CONTENT)
