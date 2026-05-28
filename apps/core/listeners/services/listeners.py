"""Listener service.

Account-scoped: `ListenerService(account_id=X)` for per-tenant operations.
Cross-tenant operations live under `ListenerService.Global`, body in
`_listeners_global.py`, attached here as a class attribute so the public
call shape (`ListenerService.Global.<op>`) is stable regardless of layout.
"""

import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from feeds.models import Feed
from feeds.services import FeedItemService, FeedService
from listeners.models import Listener
from listeners.policy import PolicyError, enforce_policy
from listeners.registry import load_config, parse_config, validate_config

from ._listeners_global import ListenerGlobal

logger = logging.getLogger("listeners")


class SeedCursor(StrEnum):
    """Accepted values for the create-time `seed_cursor` hint.

    `LATEST`: seed `last_judged_item_id` from the feed's newest item at
    create time so the listener only judges items arriving from now on
    (skip the retention backlog). Unset / `None`: cursor stays empty,
    listener judges everything in the retention window on first cycle
    (the default).

    Unknown values must 400 at the view boundary — silently falling
    through to the default defeats the opt-out the operator asked for.
    """

    LATEST = "latest"


# Soft, advisory only. Not a cap (nothing is dropped) - just the point
# where an un-paginated account-scoped list is large enough that someone
# should think about real pagination. Crossing it logs a warning; it
# never changes the result.
_LARGE_LIST_WARN_AT = 200


class ListenerService:
    """Account-scoped service for Listener reads and writes.

    Ownership model: listeners are owned by the *account*, not the
    individual user. Any user in the account can read and manage every
    listener in it; `Listener.user_id` records who created it, for audit
    and display only, it is deliberately not a read/write filter. Reads
    here scope by `account_id` alone by design.
    """

    Global = ListenerGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("ListenerService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    @staticmethod
    def validate_delivery_mode(mode: str) -> None:
        """Raise ValueError if `mode` is not a `Listener.DeliveryMode` value.

        Account-agnostic utility, exposed as a staticmethod so callers don't
        need to instantiate the service just to validate.
        """
        valid = {m.value for m in Listener.DeliveryMode}
        if mode not in valid:
            raise ValueError(f"invalid delivery_mode {mode!r}; expected one of {sorted(valid)}")

    def get(self, id: str) -> Listener:
        """Raises Listener.DoesNotExist if missing (or if owned by another account)."""
        return Listener.objects.get(id=id, account_id=self.account_id)

    def list(self) -> list[Listener]:
        """This account's listeners, newest first.

        No pagination and no cap: listeners are account-scoped config
        objects created one at a time (realistically 1-50 per account),
        not an unbounded feed, so there's nothing to bound in v0 and a
        silent cap would only hide data. If listeners ever become
        unbounded, add real pagination (cursor or limit/offset) here and
        at the endpoint, not a magic truncation.

        Ordered by the ULID PK (`-id`), not `created_at`: ULIDs sort
        lexicographically by creation time, so this is newest-first on
        the indexed primary key. See core/AGENTS.md.

        Materialized list, not a streaming iterator: the only caller
        serializes `many=True` which materializes anyway, so a chunked
        cursor bought nothing."""
        listeners = list(Listener.objects.filter(account_id=self.account_id).order_by("-id"))
        if len(listeners) >= _LARGE_LIST_WARN_AT:
            # Advisory only - nothing is truncated. Flags that this
            # account's list is large enough to warrant real pagination
            # before it becomes a problem.
            logger.warning(
                "listener list for account %s returned %d rows (>= %d); consider adding pagination",
                self.account_id,
                len(listeners),
                _LARGE_LIST_WARN_AT,
            )
        return listeners

    def _assert_feed_exists(self, config: object) -> None:
        """The listener's feed_id MUST reference a Feed in this account.
        Cross-table integrity the pure schema can't check; raises
        PolicyError (-> 400) so a listener can't point at a missing or
        cross-account feed and then fail every judge cycle."""
        feed_id = getattr(config, "feed_id", None)
        if not feed_id:
            return  # a future kind without a feed; nothing to check
        if not Feed.objects.filter(id=feed_id, account_id=self.account_id).exists():
            raise PolicyError(f"unknown feed {feed_id!r} in this account")

    def build(
        self,
        *,
        user_id: str,
        name: str,
        instructions: str,
        kind: str,
        delivery_mode: str,
        data: dict[str, Any],
    ) -> Listener:
        """Validate the inputs and return an UNSAVED Listener instance.

        Runs the full validation `create` does (DeliveryMode enum + the
        kind's Pydantic config incl. the engine-kind registry check +
        feed-exists) and normalizes `data` to the canonical Pydantic JSON
        dump, but never touches the DB. The dry-run path serializes the
        instance this returns so the preview is byte-for-byte what
        `create` would persist, no second validation path to drift.
        """
        self.validate_delivery_mode(delivery_mode)
        # Re-validate `data` even though the view layer already did. The
        # service is the seam where wrong shape becomes impossible, so
        # any caller (mgmt command, future internal flow) gets the same
        # safety net the HTTP path does.
        validated = validate_config(kind, data)
        self._assert_feed_exists(validated)
        normalized_data = validated.model_dump(mode="json")
        # Scope is enforced by construction here (account_id is bound to
        # this service instance), so `_assert_scope` isn't needed on the
        # write path the way it is for update_digest_state.
        return Listener(
            user_id=user_id,
            account_id=self.account_id,
            kind=kind,
            name=name,
            instructions=instructions,
            delivery_mode=delivery_mode,
            data=normalized_data,
        )

    def create(
        self,
        *,
        user_id: str,
        name: str,
        instructions: str,
        kind: str,
        delivery_mode: str,
        data: dict[str, Any],
        seed_cursor: str | None = None,
    ) -> Listener:
        """Validate (via `build`) then persist. `data` is stored as the
        canonical Pydantic JSON dump so downstream readers always see a
        normalized blob regardless of input ordering / omitted defaults.

        Cursor starts empty by default so a new Listener attached to a
        Feed with existing items judges them all (within the retention
        window). Pass `seed_cursor=SeedCursor.LATEST` (the string "latest")
        to skip existing items and only judge what arrives from now on;
        the cursor is set from the feed's newest item id at create time.
        """
        listener = self.build(
            user_id=user_id,
            name=name,
            instructions=instructions,
            kind=kind,
            delivery_mode=delivery_mode,
            data=data,
        )
        if seed_cursor == SeedCursor.LATEST:
            self._seed_cursor_to_latest(listener)
        listener.save()
        return listener

    def _seed_cursor_to_latest(self, listener: Listener) -> None:
        """Set `last_judged_item_id` to the feed's newest FeedItem id so
        the listener only judges items newer than what already exists.
        No-op if the listener has no `feed_id` or the feed has no items."""
        config = load_config(listener)
        feed_id = getattr(config, "feed_id", None)
        if not feed_id:
            return
        feed_svc = FeedService(account_id=self.account_id)
        try:
            feed = feed_svc.get(feed_id)
        except Feed.DoesNotExist as exc:
            # build's _assert_feed_exists check has already passed; reaching
            # here means a concurrent operator deleted the feed between
            # validate and seed. Re-raise as PolicyError → 400 so the
            # creating user gets a real error instead of a silently-dangling
            # listener with cursor='' pointing at a missing feed.
            raise PolicyError(f"feed {feed_id!r} was deleted before listener could be created") from exc
        newest = FeedItemService(account_id=self.account_id).newest_item_id(feed)
        if newest:
            listener.last_judged_item_id = newest

    def build_update(
        self,
        listener: Listener,
        /,
        *,
        name: str,
        instructions: str,
        delivery_mode: str,
        data: dict[str, Any],
    ) -> Listener:
        """Validate an edit and apply it to the EXISTING `listener`
        instance (unsaved). Mirrors `build` for the dry-run path.

        `kind` is intentionally not a parameter: it is immutable on edit
        (changing it would swap the config schema). The view rejects a
        kind change before calling this.

        Identity/audit/judgment COLUMNS (`id`, `created_at`, `user_id`,
        `last_judged_item_id`, ...) are preserved by construction: we
        mutate the fetched row in place and never reassign them. The
        config blob carries forward its own must-not-reset state via
        `merge_preserving` (masked `***` secrets; no stream watermarks
        anymore - the Feed owns those).
        """
        self._assert_scope(str(listener.account_id), "listener")
        self.validate_delivery_mode(delivery_mode)

        # parse_config = shape only (no policy). Policy runs on the MERGE
        # OUTPUT below, not on `submitted`: merge_preserving restores
        # masked secrets, so the merge output is the right object to
        # enforce. The HTTP path already policy-checked the request body
        # in the serializer; this merged-enforce is the single policy seam
        # for non-serializer callers + the catch for merge-introduced state.
        submitted = parse_config(str(listener.kind), data)
        prior = load_config(listener)
        try:
            merged = submitted.merge_preserving(prior)
        except ValueError as exc:
            # merge refuses when a masked secret can't be matched to a
            # prior webhook (notifier list changed) - never persist '***'
            # as a live secret. Surface as a 400, not a 500.
            raise PolicyError(str(exc)) from exc
        merged = enforce_policy(merged)
        self._assert_feed_exists(merged)

        listener.name = name
        listener.instructions = instructions
        listener.delivery_mode = delivery_mode
        listener.data = merged.model_dump(mode="json")
        return listener

    def update(
        self,
        listener: Listener,
        /,
        *,
        name: str,
        instructions: str,
        delivery_mode: str,
        data: dict[str, Any],
    ) -> Listener:
        """Validate (via `build_update`) then persist. Saves ONLY the
        editable fields so identity/audit/judgment columns can't be
        touched even accidentally."""
        listener = self.build_update(
            listener,
            name=name,
            instructions=instructions,
            delivery_mode=delivery_mode,
            data=data,
        )
        listener.save(
            update_fields=[
                "name",
                "instructions",
                "delivery_mode",
                "data",
                "updated_at",
            ]
        )
        return listener

    def delete(self, listener: Listener, /) -> None:
        """Delete a listener. Scope-asserted defense-in-depth even though
        `get()` already account-scoped the fetch."""
        self._assert_scope(str(listener.account_id), "listener")
        listener.delete()

    def advance_judge_cursor(self, listener: Listener, /, *, item_id: str) -> None:
        """Set the listener's judgment cursor to `item_id` (the highest
        FeedItem id considered this cycle). Items with id <= this are not
        re-judged."""
        self._assert_scope(str(listener.account_id), "listener")
        listener.last_judged_item_id = item_id
        listener.save(update_fields=["last_judged_item_id", "updated_at"])

    def rewind_judge_cursor(self, listener: Listener, /, *, to: str = "") -> None:
        """Operator action: rewind the listener's judgment cursor.

        Empty `to` (default) = re-judge every item in the feed's retention
        window on the next cycle. A specific ULID = re-judge items after
        that point. Use after an outage, after refining `instructions`, or
        for forensic re-runs. Costs LLM tokens for every re-judged item,
        so the caller (CLI) confirms before invoking by default."""
        self._assert_scope(str(listener.account_id), "listener")
        listener.last_judged_item_id = to
        listener.save(update_fields=["last_judged_item_id", "updated_at"])

    def update_digest_state(
        self,
        listener: Listener,
        /,
        *,
        last_digest_at: datetime,
        digest_interval_seconds: int,
    ) -> None:
        """Update digest bookkeeping after a delivery cycle."""
        self._assert_scope(str(listener.account_id), "listener")
        listener.last_digest_at = last_digest_at
        listener.next_digest_at = last_digest_at + timedelta(seconds=digest_interval_seconds)
        listener.save(update_fields=["last_digest_at", "next_digest_at", "updated_at"])
