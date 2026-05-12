"""LogNotifier — writes the batch to stdout. Useful for verification before wiring real webhooks."""

import json
import sys
import time

from listeners.configs import LogNotifierSpec
from notifications.services.batching import build_payload

from .base import HitBatch, NotificationResult


class LogNotifier:
    kind = "log"

    def deliver(self, batch: HitBatch, spec: LogNotifierSpec) -> NotificationResult:
        started = time.perf_counter()
        payload = build_payload(batch, include_fields=spec.include_fields)
        sys.stdout.write(f"{spec.prefix} {json.dumps(payload, default=str)}\n")
        sys.stdout.flush()
        elapsed = int((time.perf_counter() - started) * 1000)
        return NotificationResult(
            notifier_kind=self.kind, delivered=True, latency_ms=elapsed
        )
