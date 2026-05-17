"""Listener service.

Account-scoped: `ListenerService(account_id=X)` for per-tenant operations.
Cross-tenant operations live under `ListenerService.Global`, body in
`_listeners_global.py`, attached here as a class attribute so the public
call shape (`ListenerService.Global.<op>`) is stable regardless of layout.
"""

from datetime import datetime, timedelta
from typing import Any

from listeners.models import Listener
from listeners.registry import get_config_class

from ._listeners_global import ListenerGlobal

# Hard ceiling on the un-paginated list endpoint. An account is not
# expected to approach this in v0; if that changes, add real pagination
# rather than raising the cap. Bounds the response so a pathological
# account can't ask for an unbounded result set.
LISTENERS_LIST_CAP = 500


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
            raise ValueError(
                f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}"
            )

    @staticmethod
    def validate_delivery_mode(mode: str) -> None:
        """Raise ValueError if `mode` is not a `Listener.DeliveryMode` value.

        Account-agnostic utility, exposed as a staticmethod so callers don't
        need to instantiate the service just to validate.
        """
        valid = {m.value for m in Listener.DeliveryMode}
        if mode not in valid:
            raise ValueError(
                f"invalid delivery_mode {mode!r}; expected one of {sorted(valid)}"
            )

    def get(self, id: str) -> Listener:
        """Raises Listener.DoesNotExist if missing (or if owned by another account)."""
        return Listener.objects.get(id=id, account_id=self.account_id)

    def list(self) -> list[Listener]:
        """This account's listeners, newest first, capped at
        `LISTENERS_LIST_CAP`.

        Returns a materialized list, not a streaming iterator: the only
        caller serializes `many=True` which materializes anyway, so a
        chunked cursor bought nothing. No pagination in v0 (see the cap
        rationale on the constant)."""
        return list(
            Listener.objects.filter(account_id=self.account_id).order_by("-created_at")[
                :LISTENERS_LIST_CAP
            ]
        )

    def build(
        self,
        *,
        user_id: str,
        name: str,
        instructions: str,
        kind: str,
        delivery_mode: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Listener:
        """Validate the inputs and return an UNSAVED Listener instance.

        Runs the full validation `create` does (DeliveryMode enum + the
        kind's Pydantic config, which includes the engine-kind registry
        check) and normalizes `data` to the canonical Pydantic JSON
        dump, but never touches the DB. The dry-run path serializes the
        instance this returns so the preview is byte-for-byte what
        `create` would persist, no second validation path to drift.
        """
        self.validate_delivery_mode(delivery_mode)
        # Re-validate `data` even though the view layer already did. The
        # service is the seam where wrong shape becomes impossible, so
        # any caller (mgmt command, future internal flow) gets the same
        # safety net the HTTP path does.
        config_class = get_config_class(kind)
        validated = config_class.model_validate(data)
        normalized_data = validated.model_dump(mode="json")
        # Scope is enforced by construction here (account_id is bound to
        # this service instance), so `_assert_scope` isn't needed on the
        # write path the way it is for update_poll_state/update_digest.
        return Listener(
            user_id=user_id,
            account_id=self.account_id,
            kind=kind,
            name=name,
            instructions=instructions,
            delivery_mode=delivery_mode,
            poll_interval_seconds=poll_interval_seconds,
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
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Listener:
        """Validate (via `build`) then persist. `data` is stored as the
        canonical Pydantic JSON dump so downstream readers always see a
        normalized blob regardless of input ordering / omitted defaults.
        """
        listener = self.build(
            user_id=user_id,
            name=name,
            instructions=instructions,
            kind=kind,
            delivery_mode=delivery_mode,
            poll_interval_seconds=poll_interval_seconds,
            data=data,
        )
        listener.save()
        return listener

    def update_poll_state(
        self,
        listener: Listener,
        /,
        *,
        last_polled_at: datetime,
        data: dict,
    ) -> None:
        """Update poll bookkeeping. `data` is the (mutated) Pydantic config dumped to JSON."""
        self._assert_scope(str(listener.account_id), "listener")
        listener.last_polled_at = last_polled_at
        listener.next_poll_at = last_polled_at + timedelta(
            seconds=int(listener.poll_interval_seconds)
        )
        listener.data = data
        listener.save(
            update_fields=["last_polled_at", "next_poll_at", "data", "updated_at"]
        )

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
        listener.next_digest_at = last_digest_at + timedelta(
            seconds=digest_interval_seconds
        )
        listener.save(update_fields=["last_digest_at", "next_digest_at", "updated_at"])
