"""Drain one claimed watch-action run: dispatch, persist, advance.

The per-run unit under `process_due_runs`. The command does the global
work (reap stale, then iterate `claim_due`, which CAS-claims each due run
to RUNNING) ; `WatchDrainOperation` takes one already-claimed run and
carries it to a terminal state, enqueuing the next chain action when it
SUCCEEDS. Mirrors `WatchTriggerOperation` / `FeedPollOperation`: a
one-shot operation with account-scoped services, `.run()` once.

The expensive leg (hydrate + the LLM judge) runs OUTSIDE any transaction.
Only the terminal write + the next-action enqueue are wrapped in one short
atomic block, so a SUCCEEDED run and its successor commit together, no
silently stalled chain, without holding a DB lock across the judge. A
crash before that commit leaves the run RUNNING for the reaper to retry.
"""

import logging
from datetime import datetime
from functools import cached_property

from django.db import transaction
from django.utils import timezone

from feeds.models import FeedItem
from feeds.services import FeedItemService
from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages
from watches.actions import registry as actions_registry
from watches.actions.protocol import ActionOutcome
from watches.models import WatchAction, WatchActionRun
from watches.services import WatchActionRunService, WatchActionService

from .advance import enqueue_next

logger = logging.getLogger("watches")


class WatchDrainOperation:
    """One-shot: execute one CLAIMED (RUNNING) run to a terminal state."""

    def __init__(self, run: WatchActionRun, *, now: datetime | None = None) -> None:
        # Attribute is `action_run`, not `run`: a `run()` METHOD plus a
        # `self.run` attribute would shadow the method (self.run().run is
        # the row, not callable). `action_run` is also the truer name.
        self.action_run = run
        self.account_id = str(run.account_id)
        self.now = now or timezone.now()

    @cached_property
    def run_svc(self) -> WatchActionRunService:
        return WatchActionRunService(account_id=self.account_id)

    @cached_property
    def action_svc(self) -> WatchActionService:
        return WatchActionService(account_id=self.account_id)

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.account_id)

    def run(self) -> ActionOutcome | None:
        """Dispatch the run and persist the outcome ; return it, or None if
        the claim was lost (another drain re-claimed after a reap, see
        `complete`), in which case we did NOT write or advance.

        Expected resolution failures become terminal outcomes, not
        exceptions: a deleted action / pruned item / unregistered kind ->
        ERRORED, an action that raises -> FAILED (retryable). Only an
        UNEXPECTED error (e.g. the commit itself) propagates, for the caller
        to log and leave to the reaper. The terminal write is a guarded CAS;
        the chain only advances (next action enqueued in the SAME txn) when
        that write WON, so a stale completer never double-enqueues."""
        action, outcome = self._resolve()
        with transaction.atomic():
            committed = self.run_svc.complete(
                self.action_run, state=outcome.state, result=outcome.result, error=outcome.error, now=self.now
            )
            if committed is None:
                return None  # lost the claim; the fresh winner owns the advance
            if outcome.state == WatchActionRunState.SUCCEEDED and action is not None:
                # Advance to the next action (instant now, or into a digest
                # window) ; same helper the flush uses, so the path is shared.
                enqueue_next(self.action_run, action, now=self.now)
        return outcome

    def _resolve(self) -> tuple[WatchAction | None, ActionOutcome]:
        """Load the run's action + item, dispatch to the kind's impl, and
        return (action, outcome). The action is returned even on a
        downstream failure so `run` can resolve 'next in chain' if it ever
        needs to ; it is None only when the action row itself is gone.

        Every `error` here is a sanitized `run_messages` string (the field
        is operator-facing) ; the raw cause goes to the log keyed by run id."""
        run = self.action_run
        try:
            action = self.action_svc.get(str(run.action_id))
        except WatchAction.DoesNotExist:
            logger.warning("run=%s action=%s no longer exists", run.id, run.action_id)
            return None, ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.ACTION_GONE)
        try:
            item = self.feed_item_svc.get(str(run.feed_item_id))
        except FeedItem.DoesNotExist:
            logger.warning("run=%s feed_item=%s no longer exists", run.id, run.feed_item_id)
            return action, ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.ITEM_GONE)
        try:
            impl = actions_registry.get(action.kind)
        except KeyError:
            logger.warning("run=%s has no executor for kind=%s", run.id, action.kind)
            return action, ActionOutcome(state=WatchActionRunState.ERRORED, error=run_messages.NO_EXECUTOR)
        try:
            outcome = impl.run(action, item_data=item.data)
        except Exception as exc:
            # Protocol contract: an impl raises only on UNEXPECTED failure ->
            # retryable FAILED (the attempt was already burned at claim). Raw
            # cause to the log; the run carries only the sanitized note.
            logger.exception("run=%s kind=%s failed: %s", run.id, action.kind, exc)
            return action, ActionOutcome(state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT)
        return action, outcome
