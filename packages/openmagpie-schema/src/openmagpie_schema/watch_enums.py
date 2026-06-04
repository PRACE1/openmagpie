"""Watch-domain discriminator enums (shared, zero-Django).

The Python-side source of truth for the watch `kind` / run `state` /
delivery cadence values. They live HERE (not server-only) so both the
server AND the magpie CLI type against the same enum instead of branching
on magic strings ("no state magic strings" convention). The server's
`watches.constants` re-exports these so `watches.constants.X` keeps
working ; the DB columns stay bare CharFields (no `choices=`), so adding /
removing a value never forces a migration.
"""

from enum import StrEnum


def choices(enum: type[StrEnum]) -> str:
    """Pipe-joined enum values for help text / error messages, derived from
    the enum so a hand-listed copy can't drift ("no state magic strings")."""
    return " | ".join(e.value for e in enum)


class WatchActionKind(StrEnum):
    """The kind of node in a watch's action chain ; selects the impl + the
    config/result contract.

    Two families:
      - FILTER:   semantic_filter ; gates the chain (a pass=false GATES).
      - DELIVERY: webhook, log ; emit the item outward. Delivery cadence
                  (instant vs digest) is a `delivery` field in the action's
                  config, NOT a separate kind.
    """

    SEMANTIC_FILTER = "semantic_filter"
    WEBHOOK = "webhook"
    LOG = "log"


class WatchActionDelivery(StrEnum):
    """Cadence of a DELIVERY action (webhook / log): emit per item, or
    batch a window into one emission. A field in the action's config, not
    a separate kind."""

    INSTANT = "instant"
    DIGEST = "digest"


class WatchActivityWindow(StrEnum):
    """Bounded time windows for the action-activity summary, selected by a
    client and resolved to concrete `(since, until)` bounds SERVER-side (one
    source of truth, server clock). Applied by run EVALUATION time
    (`completed_at`). No unbounded 'all' value: a count is always over a
    finite range. Default is `WEEK`.
    """

    DAY = "24h"
    YESTERDAY = "yesterday"
    WEEK = "7d"
    MONTH = "30d"


class WatchActionRunState(StrEnum):
    """Lifecycle of one WatchActionRun (one action executing one item).

    The runner advances the chain to the next action IFF a run reaches
    `SUCCEEDED`. The control-flow fact lives in this column so the audit
    log is self-documenting (the score etc. stay in `result`).

    Terminal states, and how the drain treats each:
      - SUCCEEDED : ran, score met threshold -> advance the chain.
      - GATED     : ran cleanly, score below threshold -> chain stops. Not
                    a failure ; the expected "didn't pass the filter" path.
      - FAILED    : a TRANSIENT error (engine down, timeout, bad response).
                    Retryable -> the drain re-claims it until attempts hit
                    WATCH_RUN_MAX_ATTEMPTS, then it stays FAILED.
      - ERRORED   : a PERMANENT backend defect (e.g. a feed item whose
                    stored data can't be rehydrated). Terminal, NEVER
                    retried ; distinct from FAILED so an audit query can
                    tell "broken, look at it" from "transient, gave up".
      - SKIPPED   : a DELIBERATE non-run (operator paused the watch, a
                    policy chose not to run this action). Reserved for that
                    intent ; NOT used for defects (use ERRORED).
    Non-terminal: PENDING (queued) -> RUNNING (claimed by the drain).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    GATED = "gated"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"
