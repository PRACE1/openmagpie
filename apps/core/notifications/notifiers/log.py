"""LogNotifier: writes a human-readable batch summary to stdout.

For dev / verification before wiring real webhooks, so it renders for a
human reading the dev-tick output, not for a machine. Every hit is shown
(nothing truncated or capped), grouped by source, one line each. The full
per-observation JSON (body `content`, all source extras, `include_fields`
whitelisting) is the webhook notifier's job; dumping it here is what made
the dev-tick log unreadable.
"""

import sys
import time

from listeners.configs import LogNotifierSpec
from notifications.services.batching import build_payload

from .base import HitBatch, NotificationResult


class LogNotifier:
    kind = "log"

    def deliver(self, batch: HitBatch, spec: LogNotifierSpec) -> NotificationResult:
        started = time.perf_counter()
        sys.stdout.write(_render(batch, spec))
        sys.stdout.flush()
        elapsed = int((time.perf_counter() - started) * 1000)
        return NotificationResult(notifier_kind=self.kind, delivered=True, latency_ms=elapsed)


def _render(batch: HitBatch, spec: LogNotifierSpec) -> str:
    # Reuse build_payload only for its source grouping (the same grouping the
    # webhook sends); render title + url, never the full observation body.
    payload = build_payload(batch, include_fields=["title", "url"])
    lines = [f"{spec.prefix} {payload['listener_name']} | {payload['total_hits']} hits | {_period(batch)}"]
    for source, hits in payload["hits_by_source"].items():
        lines.append(f"  {source} ({len(hits)})")
        for hit in hits:
            lines.append(f"    - {_oneline(hit.get('title', ''))} | {hit.get('url', '')}")
    return "\n".join(lines) + "\n"


def _period(batch: HitBatch) -> str:
    end = batch.period_end.strftime("%Y-%m-%d %H:%M")
    if batch.period_start is None:
        return f"instant @ {end} UTC"
    return f"{batch.period_start.strftime('%Y-%m-%d %H:%M')} to {end} UTC"


def _oneline(text: str) -> str:
    # Collapse any newlines / whitespace runs so one hit stays on one line.
    # Not truncation: the full title is preserved, just flattened.
    return " ".join(text.split())
