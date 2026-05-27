"""notifications.services, public surface.

from notifications.services import DeliveryService
svc = DeliveryService(account_id=X)
svc.deliver_instant(event, observation, listener, config)
"""

from .delivery import DeliveryService, DigestResult

__all__ = ["DeliveryService", "DigestResult"]
