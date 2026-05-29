"""Payload-sample preview: delivery dry-run for a listener.

Composes the same code paths a real delivery cycle takes ; EventService
for recent hits, the registered Observation class for the listener's
feed source, every configured notifier's `render()` ; but skips the
ship step. The result is what each receiver WOULD see for the next
batch on this listener.

Lives in the listener service layer because the SUBJECT of a preview
is the listener: "what would this listener emit?" The notifier is a
participant, not the owner. Mirrors the shape of the other listener
services (`judgment.py`, `listeners.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from events.observations import Observation
from events.registry import UnhydrateableObservation, class_for_source, hydrate_data
from events.services import EventKind, EventService
from feeds.models import Feed, Source
from feeds.registry import load_config as load_feed_config
from feeds.services import FeedService
from listeners.models import Listener
from notifications import registry as notifiers_registry
from notifications.notifiers.base import Hit, HitBatch
from openmagpie_schema.configs import SemanticListenerConfig

logger = logging.getLogger("listeners")

# Engine relevance scores used to backfill synthetic preview hits.
# Stair-stepped so two adjacent preview entries read as visually distinct.
_SYNTH_SCORES: tuple[float, ...] = (0.87, 0.93)


class CannotPreviewSource(Exception):
    """No Observation class honestly matches the listener's feed source.

    Raised when the resolver can't pick a class to synthesize a sample
    from ; feed missing, feed config drifted out of schema, or none of
    the feed's source kinds have a registered connector in this
    deployment. View layer translates to a 409 APIException; logs name
    the specific case for operators.
    """


@dataclass(frozen=True)
class NotifierPreview:
    """One configured notifier's dry-run output for the preview batch.
    `rendered` is notifier-defined (webhook: dict, log: str)."""

    kind: str
    target: str | None
    rendered: Any


@dataclass(frozen=True)
class PreviewResult:
    """Dry-run delivery result: one NotifierPreview per configured
    notifier, plus a global `synthetic` flag set when ANY backfilled
    hits were synthesized (real hits were short of target)."""

    synthetic: bool
    notifiers: list[NotifierPreview]


def build_preview(
    listener: Listener,
    config: SemanticListenerConfig,
    *,
    account_id: str,
    now: datetime | None = None,
) -> PreviewResult:
    """Run a dry-run delivery cycle for the listener.

    Pulls up to `target` real recent hits (1 for instant, 2 for digest
    to show batching shape), backfills synthetic Observations if real
    hits are short, then renders the batch through every configured
    notifier. Each notifier returns its native shape.

    Raises `CannotPreviewSource` when backfill is needed but no
    Observation class honestly matches the listener's feed source.

    `now` is injectable for tests; production callers omit it.
    """
    is_instant = listener.delivery_mode == Listener.DeliveryMode.INSTANT
    target = 1 if is_instant else 2

    hits, synthetic = _build_hits(listener, config, target=target, account_id=account_id)

    period_end = now or timezone.now()
    period_start = _period_start(listener, config, period_end=period_end, is_instant=is_instant)
    batch = HitBatch(listener=listener, hits=hits, period_start=period_start, period_end=period_end)

    notifiers = [
        NotifierPreview(
            kind=spec.kind,
            target=notifiers_registry.get(spec.kind).target_for(spec),
            rendered=notifiers_registry.get(spec.kind).render(batch, spec),
        )
        for spec in config.notifiers
    ]
    return PreviewResult(synthetic=synthetic, notifiers=notifiers)


def _build_hits(
    listener: Listener,
    config: SemanticListenerConfig,
    *,
    target: int,
    account_id: str,
) -> tuple[list[Hit], bool]:
    """Up to `target` Hits, real-first with synthetic backfill.

    Real hits come from the most recent `target` HIT Events on this
    listener (un-hydrateable rows are skipped). If real hits are short,
    backfill with `cls.sample(variant=i)` for the missing slots ; the
    connector owns variation across variants; this helper just asks
    for variant 0, 1, .... Returns `(hits, synthetic_used)`.

    Raises `CannotPreviewSource` when backfill is needed but
    `_observation_class_for` can't honestly resolve a class for the
    listener's source.
    """
    events = EventService(account_id=account_id).list_recent_for_listener(
        kind=EventKind.HIT, listener_id=str(listener.id), limit=target
    )
    hits: list[Hit] = []
    for event in events:
        try:
            obs = hydrate_data(event.data)
        except UnhydrateableObservation:
            continue
        hits.append(Hit(obs=obs, relevance_score=event.score))
    if len(hits) >= target:
        return hits, False
    cls = _observation_class_for(config, account_id=account_id)
    if cls is None:
        raise CannotPreviewSource()
    for variant in range(len(hits), target):
        hits.append(Hit(obs=cls.sample(variant=variant), relevance_score=_SYNTH_SCORES[variant]))
    return hits, True


def _period_start(
    listener: Listener,
    config: SemanticListenerConfig,
    *,
    period_end: datetime,
    is_instant: bool,
) -> datetime | None:
    """Period start for the preview HitBatch.

    Instant batches have no period (None). Digest listeners that have
    already delivered once carry their real `last_digest_at`. Fresh
    digest listeners get a fabricated `period_end - digest_interval`
    so the preview matches the steady-state shape (real digests
    post-first-delivery carry a non-null period_start; emitting null
    here would mis-shape receivers wiring against the preview).
    """
    if is_instant:
        return None
    if listener.last_digest_at is not None:
        return listener.last_digest_at
    return period_end - timedelta(seconds=config.digest_interval_seconds)


def _observation_class_for(config: SemanticListenerConfig, *, account_id: str) -> type[Observation] | None:
    """Pick the Observation class for the listener's feed source, or
    None if no honest match is available. Caller surfaces None as a
    CannotPreviewSource.

    Three None paths (each warn-logged so operators can diagnose):
      - feed row missing
      - feed config no longer validates against the current schema
      - feed loads fine but none of its source `spec.kind`s have a
        registered Observation class (connector renamed/removed/unloaded)
    """
    try:
        feed = FeedService(account_id=account_id).get(config.feed_id)
        load_feed_config(feed)  # surface kind-registry + schema drift early
    except Feed.DoesNotExist:
        logger.warning("payload-sample: feed %s not found (stale feed_id?)", config.feed_id)
        return None
    except PydanticValidationError as exc:
        logger.warning(
            "payload-sample: feed %s config does not validate against current schema: %s",
            config.feed_id,
            exc,
        )
        return None
    except KeyError as exc:
        # feeds.registry.get_config_class does a bare `_REGISTRY[kind]`,
        # so a feed row whose `kind` was renamed/removed in code raises
        # KeyError out of load_feed_config. Treat the same as drift ;
        # the operator's preview-time view of "what's broken" is what
        # matters here.
        logger.warning("payload-sample: feed %s kind not registered: %s", config.feed_id, exc)
        return None
    # Source kinds live on the Source table now (not feed.data); pull
    # the distinct kinds the feed actually polls and pick the first
    # one with a registered Observation class.
    source_kinds = list(
        Source.objects.filter(account_id=account_id, feed_id=str(feed.id)).values_list("kind", flat=True).distinct()
    )
    for kind in source_kinds:
        cls = class_for_source(kind)
        if cls is not None:
            return cls
    logger.warning(
        "payload-sample: feed %s sources [%s] have no registered Observation class",
        config.feed_id,
        ", ".join(source_kinds) or "(none)",
    )
    return None
