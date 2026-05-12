"""Cross-tenant Delivery operations.

Do NOT import from this file directly — use `DeliveryService.Global.<op>(...)`.
The leading underscore signals "implementation detail of delivery.py"; the
`.Global` namespace on the service class is the stable public surface.

Reach for these sparingly — scheduler entry points + admin / debug commands.
"""

from common.locks import digest_lock
from listeners.configs import SemanticListenerConfig
from listeners.models import Listener


class DeliveryGlobal:
    """Static methods only. Span all accounts.

    Thin sugar over `DeliveryService(account_id=listener.account_id)` for
    scheduler loops that already hold a listener and don't want to repeat
    the account derivation. Acquires the per-listener digest lock so
    concurrent processes can't double-deliver.
    """

    @staticmethod
    def deliver_digest(
        listener: Listener, config: SemanticListenerConfig
    ) -> int | None:
        """Locked entry point for one Listener's digest cycle.

        Acquires `digest_lock(listener.id)`; returns None if another process
        already holds it. Otherwise returns the number of hits delivered
        (0 on partial / total failure of the notifiers — the Events stay
        pending and the next cycle re-batches them).

        Tests / direct access should call
        `DeliveryService(account_id=...).deliver_digest(...)` to bypass.
        """
        from .delivery import DeliveryService  # lazy: avoid circular import

        with digest_lock(str(listener.id)) as acquired:
            if not acquired:
                return None
            return DeliveryService(account_id=str(listener.account_id)).deliver_digest(
                listener, config
            )
