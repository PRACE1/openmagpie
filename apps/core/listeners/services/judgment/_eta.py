"""ETA helpers for the judge cycle.

`_est_seconds_per_item` and `_record_judge_latency` maintain a
per-listener EWMA of recent judge latencies, used for the up-front
"this cycle will take ~Ns" estimate. `_running_eta_seconds` computes
the in-cycle ETA from the running mean once a few items have landed
(more accurate than the cross-cycle EWMA, which is just the seed
prior on the first cycle).

State is process-local: lost on container restart, converges within a
few items on the next cycle. Avoids a schema migration for a UI nicety.
"""

from listeners.models import Listener

# Per-listener EWMA of judge latency (seconds). Process-local.
_LATENCY_EWMA_ALPHA = 0.3
_LATENCY_SEED_SECONDS = 2.0
_listener_latency_ewma: dict[str, float] = {}


def _est_seconds_per_item(listener: Listener) -> float:
    """Per-listener mean of recent judge latencies (EWMA), or the seed
    default when no history exists yet (fresh process / first cycle)."""
    return _listener_latency_ewma.get(str(listener.id), _LATENCY_SEED_SECONDS)


def _record_judge_latency(listener: Listener, latency_ms: int) -> None:
    """Fold one observed latency into the listener's EWMA."""
    key = str(listener.id)
    seconds = latency_ms / 1000.0
    prev = _listener_latency_ewma.get(key)
    _listener_latency_ewma[key] = (
        seconds if prev is None else _LATENCY_EWMA_ALPHA * seconds + (1 - _LATENCY_EWMA_ALPHA) * prev
    )


def _running_eta_seconds(
    pending: int,
    processed: int,
    judged: int,
    cycle_latency_ms: int,
    listener: Listener,
) -> int:
    """ETA in seconds for the rest of the current cycle.

    Uses the in-cycle mean per successful judge when we have data
    (`judged > 0`); falls back to the listener's cross-cycle EWMA for
    the all-errors-so-far edge case. Cost is `remaining * mean`, with
    `remaining = pending - processed` so error items don't inflate
    the remaining count."""
    remaining = max(0, pending - processed)
    if remaining == 0:
        return 0
    mean_seconds = (cycle_latency_ms / judged / 1000.0) if judged > 0 else _est_seconds_per_item(listener)
    return max(0, round(remaining * mean_seconds))
