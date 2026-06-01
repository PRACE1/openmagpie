"""Watch-domain discriminators.

StrEnums, NOT Django `TextChoices` ; we deliberately keep `choices=` OFF
the DB columns (a new enum value would otherwise force a migration). The
column is a bare CharField storing the string value; these enums are the
Python-side source of truth for validation + branching. Mirrors the
`auth_api.constants.DeviceSessionStatus` pattern.
"""

from enum import StrEnum


class WatchActionKind(StrEnum):
    """The kind of node in a watch's action chain. The action registry
    (later commit) maps each kind to its implementation + its strict
    config / result contract.

    Two families:
      - FILTER:   semantic_filter ; gates the chain (a pass=false GATES).
      - DELIVERY: webhook, log ; emit the item outward. Delivery cadence
                  (instant vs digest) is a `delivery` field in the action's
                  config, NOT a separate kind ; a digest is just an
                  outbound action batching a window of items into one
                  emission. (No `choices=` on the column means adding /
                  removing a kind here never needs a migration.)
    """

    SEMANTIC_FILTER = "semantic_filter"
    WEBHOOK = "webhook"
    LOG = "log"


class WatchActionRunState(StrEnum):
    """Lifecycle of one WatchActionRun (one action executing against one item).

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
