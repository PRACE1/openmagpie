"""Feeds API wire shapes.

Input: `FeedCreateSerializer` (the HTTP request-validation boundary,
delegating the `data` blob to the Pydantic registry). Output: plain
builders (`feed_wire` / `feed_view` / `feed_mutation`) populating the
shared `openmagpie_schema.feed` models, so the server is their authority
and the CLI imports the same classes. Mirrors `listeners.serializers`.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from common.pydantic_errors import pydantic_errors_to_drf
from feeds.models import Feed, FeedItem
from feeds.policy import PolicyError
from feeds.registry import get_config_class, load_config, validate_config
from openmagpie_schema.feed import (
    FeedConfigSummary,
    FeedItemWire,
    FeedMutationResponse,
    FeedView,
    FeedWire,
)

from .models.feed import MIN_POLL_INTERVAL_SECONDS

logger = logging.getLogger("feeds")


# ── Input ──────────────────────────────────────────────────────────────


class FeedCreateSerializer(serializers.Serializer):
    """Envelope for POST /v1/feeds. The kind-specific config (streams +
    retention) arrives as `data`, validated via the Pydantic registry."""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    kind = serializers.CharField(max_length=32, default="curated")
    poll_interval_seconds = serializers.IntegerField(min_value=MIN_POLL_INTERVAL_SECONDS, default=300)
    data = serializers.DictField(child=serializers.JSONField())

    def validate_kind(self, value: str) -> str:
        try:
            get_config_class(value)
        except KeyError:
            raise serializers.ValidationError(f"unknown feed kind {value!r}") from None
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # validate_config = shape (Pydantic) + policy (no future watermark,
        # retention bounds). Each failure maps to its own 400 shape.
        try:
            validated = validate_config(attrs["kind"], attrs["data"])
        except PydanticValidationError as exc:
            raise serializers.ValidationError({"data": pydantic_errors_to_drf(exc)}) from exc
        except PolicyError as exc:
            raise serializers.ValidationError({"data": [str(exc)]}) from exc
        attrs["data"] = validated.model_dump(mode="json")
        return attrs


# ── Output ─────────────────────────────────────────────────────────────

_EMPTY_SUMMARY = FeedConfigSummary()


def _redacted_data(feed: Feed) -> dict[str, Any]:
    """`feed.data` validated through the kind's typed config and redacted.
    Per-row fail-safe (mirrors listeners): a corrupt row degrades to a
    sentinel, never 500s a `many` list."""
    try:
        return load_config(feed).redacted_dump()
    except Exception:
        logger.exception("feed %s data failed redaction (kind=%s); returning sentinel", feed.id, feed.kind)
        return {"error": "config_unreadable"}


def _feed_summary(feed: Feed) -> FeedConfigSummary:
    """Display projection from the typed config; same per-row fail-safe."""
    try:
        return load_config(feed).summary()
    except Exception:
        return _EMPTY_SUMMARY


def _feed_item_wire(item: FeedItem) -> FeedItemWire:
    # stream: the item's sub-source label (e.g. "r/ClaudeCowork"), read
    # directly from the row — no parsing, no fallback.
    return FeedItemWire(
        id=str(item.id),
        source=str(item.source),
        stream=str(item.stream_label),
        external_id=str(item.external_id),
        occurred_at=item.occurred_at,
        data=item.data or {},
    )


def feed_wire(feed: Feed) -> FeedWire:
    """Single source for a Feed's kind-independent wire envelope. Tolerates
    an unsaved instance (dry-run): created/poll timestamps None, id empty."""
    return FeedWire(
        id=str(feed.id),
        name=feed.name,
        kind=str(feed.kind),
        is_active=feed.is_active,
        poll_interval_seconds=feed.poll_interval_seconds,
        last_polled_at=feed.last_polled_at,
        next_poll_at=feed.next_poll_at,
        user_id=str(feed.user_id),
        data=_redacted_data(feed),
        created_at=feed.created_at,
    )


def feed_view(feed: Feed, *, recent_items: list[FeedItem] | None = None) -> FeedView:
    """GET-detail response: envelope + summary + the recent item log
    ("sort by new and go")."""
    items = recent_items or []
    return FeedView(
        **feed_wire(feed).model_dump(),
        summary=_feed_summary(feed),
        recent_items=[_feed_item_wire(i) for i in items],
    )


def feed_mutation(feed: Feed, *, dry_run: bool) -> FeedMutationResponse:
    """Create / edit response: envelope + summary + dry_run."""
    return FeedMutationResponse(
        **feed_wire(feed).model_dump(),
        summary=_feed_summary(feed),
        dry_run=dry_run,
    )
