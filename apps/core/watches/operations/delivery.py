"""Build the enriched delivery inputs a DeliveryAction consumes.

Turns the runs + their FeedItems + the owning Watch into the `DeliveryItem`
list and `DeliveryContext` the drain (instant) and the flush (digest) hand to
`deliver()`. READS only (source label/kind off the FeedItem, the relevance
score off each run's upstream semantic-filter run, the watch name) ; the
operations layer owns the writes.
"""

from __future__ import annotations

from datetime import datetime

from feeds.models import FeedItem
from openmagpie_schema.watch_enums import WatchActionDelivery
from watches.actions.protocol import DeliveryContext, DeliveryItem, item_key
from watches.models import Watch, WatchActionRun
from watches.services import WatchActionRunService, WatchService


def build_delivery_inputs(
    pairs: list[tuple[WatchActionRun, FeedItem]],
    *,
    watch_id: str,
    delivery: WatchActionDelivery,
    run_svc: WatchActionRunService,
    window_since: datetime | None = None,
    window_until: datetime | None = None,
) -> tuple[list[DeliveryItem], DeliveryContext]:
    """`(items, context)` for one delivery: one `(run, item)` pair for instant,
    N for a digest batch. The score for each item is its run's upstream
    semantic-filter score (via `prior_run_id`), None when no filter preceded."""
    scores = _scores_for(run_svc, [run for run, _ in pairs])
    items = [
        DeliveryItem(
            data=item.data,
            key=item_key(item.data),
            source_label=item.source_label,
            source_kind=item.source_kind,
            score=scores.get(str(run.id)),
        )
        for run, item in pairs
    ]
    context = DeliveryContext(
        watch_id=watch_id,
        watch_name=_watch_name(watch_id),
        delivery=delivery,
        window_since=window_since,
        window_until=window_until,
    )
    return items, context


def _scores_for(run_svc: WatchActionRunService, runs: list[WatchActionRun]) -> dict[str, float | None]:
    """Map each run id -> the score on its prior (semantic-filter) run, in one
    bulk query. A run with no prior (delivery is the chain head) maps to None."""
    prior_ids = [str(run.prior_run_id) for run in runs if run.prior_run_id]
    results = run_svc.results_by_id(prior_ids) if prior_ids else {}
    out: dict[str, float | None] = {}
    for run in runs:
        result = results.get(str(run.prior_run_id)) if run.prior_run_id else None
        score = result.get("score") if isinstance(result, dict) else None
        out[str(run.id)] = float(score) if isinstance(score, int | float) else None
    return out


def _watch_name(watch_id: str) -> str:
    """The watch's display name for the payload ; empty if it was deleted
    out from under an in-flight delivery (a benign race, not a failure)."""
    try:
        return WatchService.Global.get(watch_id).name
    except Watch.DoesNotExist:
        return ""
