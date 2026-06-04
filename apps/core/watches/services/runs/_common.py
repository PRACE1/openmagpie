"""Shared run-state constants for the `runs` subpackage (drain + service)."""

from openmagpie_schema.watch_enums import WatchActionRunState

_PENDING = WatchActionRunState.PENDING.value
_RUNNING = WatchActionRunState.RUNNING.value
_FAILED = WatchActionRunState.FAILED.value

# The states a stalled/failed run can be re-claimed from. ERRORED and
# SKIPPED are terminal (permanent defect / deliberate) ; GATED + SUCCEEDED
# are clean terminals. Only PENDING (never run) and FAILED (transient,
# retryable) are claimable.
_CLAIMABLE = (_PENDING, _FAILED)

# Trigger-enqueue chunk: feed-item ids per SELECT-have + bulk_create round.
# Bounds BOTH the in-memory footprint (the `have` set + row list) AND the
# INSERT size — same unit of work, so ONE constant (splitting invites drift).
# Chunk <= this, so bulk_create needs no own `batch_size`: one chunk = one
# INSERT. A module constant (internal perf knob, no per-deployment meaning).
_ENQUEUE_CHUNK = 500
