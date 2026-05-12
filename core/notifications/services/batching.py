"""Payload construction for outgoing notifier payloads.

Groups hits by `{source}:{slug}` so the recipient sees aggregated context
("found these from r/X, those from r/Y") instead of N independent items.

Each hit's fields are pulled from the typed Observation's full `model_dump()`,
so source-specific extras (Reddit's `score`, `subreddit`, `permalink`, etc.)
are available by default. Internal scoping fields (`user_id`, `account_id`)
are always stripped. Notifiers can override with `include_fields` to whitelist.
"""

from typing import Any

from events.observations import Observation
from notifications.notifiers.base import HitBatch

# Fields stripped from every payload — they're internal scoping, not for downstream consumers.
_EXCLUDED_FIELDS: frozenset[str] = frozenset({"user_id", "account_id"})


def _stream_key(obs: Observation) -> str:
    """The grouping key for hits — `<source>:<slug>` if the observation has a slug, else just source.
    Slug knowledge lives on the Observation subclass (see `Observation.stream_slug`)."""
    slug = obs.stream_slug()
    return f"{obs.source}:{slug}" if slug else obs.source


def _hit_dict(obs: Observation, include_fields: list[str]) -> dict[str, Any]:
    """Build the per-hit dict.

    `include_fields` semantics:
      - empty list (default): include all observation fields except scoping
      - list of names: whitelist (only the listed fields)
    Unknown field names in the whitelist are silently skipped.
    """
    full = obs.model_dump(mode="json")
    for field in _EXCLUDED_FIELDS:
        full.pop(field, None)
    if not include_fields:
        return full
    return {k: full[k] for k in include_fields if k in full}


def build_payload(
    batch: HitBatch, *, include_fields: list[str] | None = None
) -> dict[str, Any]:
    """Group hits by stream key into a single payload dict, ready for JSON encoding."""
    selected = list(include_fields or [])
    by_source: dict[str, list[dict[str, Any]]] = {}
    for obs in batch.hits:
        by_source.setdefault(_stream_key(obs), []).append(_hit_dict(obs, selected))

    return {
        "listener_id": str(batch.listener.id),
        "listener_name": str(batch.listener.name),
        "period_start": batch.period_start.isoformat() if batch.period_start else None,
        "period_end": batch.period_end.isoformat(),
        "total_hits": len(batch.hits),
        "hits_by_source": by_source,
    }
