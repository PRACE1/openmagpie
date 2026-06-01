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


class WatchActionRunState(StrEnum):
    """Lifecycle of one WatchActionRun (one action executing one item).

    The runner advances the chain to the next action IFF a run reaches
    `SUCCEEDED`. `GATED` is a clean run whose result halts the chain (a
    semantic-filter that returned pass=false) ; the control-flow fact
    lives in this column so the audit log is self-documenting, with the
    score still in `result`.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    GATED = "gated"
    FAILED = "failed"
    SKIPPED = "skipped"
