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
from notifications.notifiers.base import Hit, HitBatch

# Fields stripped from every payload, they're internal scoping, not for downstream consumers.
_EXCLUDED_FIELDS: frozenset[str] = frozenset({"user_id", "account_id"})


def _source_key(obs: Observation) -> str:
    """The grouping key for hits, `<source>:<slug>` if the observation has a slug, else just source.
    Slug knowledge lives on the Observation subclass (see `Observation.source_slug`)."""
    slug = obs.source_slug()
    return f"{obs.source}:{slug}" if slug else obs.source


def _hit_dict(hit: Hit, include_fields: list[str]) -> dict[str, Any]:
    """Build the per-hit dict.

    The Observation's full dump is the base (minus internal scoping). The
    engine's `relevance_score` is layered on AFTER the obs dump as a
    separate key ; so it doesn't collide with source-side `score` (e.g.
    Reddit upvote count) and operators can whitelist them independently.

    `relevance_score` is always emitted (as `null` when the score is
    unknown ; e.g. an older row predating the column, or a future non-hit
    event kind). Heterogeneous shapes (key present in some entries,
    absent in others) within one payload would force receivers to defend
    against KeyError on a whitelist-pinned field; emitting `null`
    consistently keeps the payload shape stable per listener.

    `include_fields` semantics:
      - empty list (default): include all observation fields + relevance_score
      - list of names: whitelist (only the listed fields)
    Unknown field names in the whitelist are silently skipped.
    """
    full = hit.obs.model_dump(mode="json")
    for field in _EXCLUDED_FIELDS:
        full.pop(field, None)
    # The class-time guard (`Observation.__pydantic_init_subclass__`)
    # rejects subclass-declared `relevance_score` fields, but exotic
    # paths (`model_config['extra']='allow'` accepting a vendor
    # field, or `Field(serialization_alias='relevance_score')` on an
    # unrelated attribute) can still emit the key. Belt-and-suspenders:
    # refuse to silently clobber a source value with the engine score.
    if "relevance_score" in full:
        raise ValueError(
            f"Observation {type(hit.obs).__name__} emitted a 'relevance_score' key via "
            "model_dump (extra='allow' or serialization_alias), which would be silently "
            "overwritten by the engine score. Rename the source field."
        )
    full["relevance_score"] = hit.relevance_score
    if not include_fields:
        return full
    return {k: full[k] for k in include_fields if k in full}


def build_payload(batch: HitBatch, *, include_fields: list[str] | None = None) -> dict[str, Any]:
    """Group hits by source key into a single payload dict, ready for JSON encoding."""
    selected = list(include_fields or [])
    by_source: dict[str, list[dict[str, Any]]] = {}
    for hit in batch.hits:
        by_source.setdefault(_source_key(hit.obs), []).append(_hit_dict(hit, selected))

    return {
        "listener_id": str(batch.listener.id),
        "listener_name": str(batch.listener.name),
        "period_start": batch.period_start.isoformat() if batch.period_start else None,
        "period_end": batch.period_end.isoformat(),
        "total_hits": len(batch.hits),
        "hits_by_source": by_source,
    }
