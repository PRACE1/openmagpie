"""WatchActionRunService: enqueue + claim + complete watch-action runs.

The stateful queue under the trigger/drain crons:
  - the TRIGGER (`process_due_watches`) calls `enqueue` to create the
    first PENDING run for a new feed item.
  - the DRAIN (`process_due_runs`) calls `Global.reap_stale`, then
    `Global.claim_due` (a CAS that flips PENDING/FAILED -> RUNNING and
    burns an attempt), runs the action, and calls `complete` (terminal
    state + result) ; on SUCCEEDED it `enqueue`s the next chain action.

Both `claim_due` and `complete` are compare-and-swap UPDATEs keyed on
(state, attempts), so the row IS the lock at BOTH ends: overlapping drains
can't double-claim, and a drain whose claim was reaped + re-taken mid-judge
can't double-complete (its stale `complete` matches no row and returns
None, so it never advances the chain). A run that crashes mid-flight is
left RUNNING and recovered by `reap_stale`. Retries are bounded by
`attempts < WATCH_RUN_MAX_ATTEMPTS`.

Split across the subpackage: `_common` (state constants), `_drain`
(`WatchActionRunGlobal` + the `_due_runs` filter, the cross-tenant cron
surface), `_service` (`WatchActionRunService`, the account-scoped surface).
"""

from ._drain import WatchActionRunGlobal
from ._service import WatchActionRunService

__all__ = ["WatchActionRunGlobal", "WatchActionRunService"]
