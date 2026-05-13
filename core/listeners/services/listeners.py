"""Listener service.

Account-scoped: `ListenerService(account_id=X)` for per-tenant operations.
Cross-tenant operations live under `ListenerService.Global`, body in
`_listeners_global.py`, attached here as a class attribute so the public
call shape (`ListenerService.Global.<op>`) is stable regardless of layout.
"""

from datetime import datetime, timedelta

from listeners.models import Listener

from ._listeners_global import ListenerGlobal


class ListenerService:
    """Account-scoped service for Listener reads and writes."""

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
