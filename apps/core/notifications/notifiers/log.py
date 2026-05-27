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

    def render(self, batch: HitBatch, spec: LogNotifierSpec) -> str:
        """The multi-line text block this notifier WOULD write to
        stdout for the batch. payload-sample calls this; `deliver` calls
        it too so previews can't drift from production."""
        return _render_text(batch, spec)

    def target_for(self, spec: LogNotifierSpec) -> None:
        # Writes to a fixed sink (server stdout). No destination URL.
        return None

    def deliver(self, batch: HitBatch, spec: LogNotifierSpec) -> NotificationResult:
        started = time.perf_counter()
        sys.stdout.write(self.render(batch, spec))
        sys.stdout.flush()
        elapsed = int((time.perf_counter() - started) * 1000)
        return NotificationResult(notifier_kind=self.kind, delivered=True, latency_ms=elapsed)


# Per-hit fields shown when the operator doesn't override `include_fields`.
# Kept short on purpose — log mode is for a human scanning dev-tick output,
# not for a machine. Operators wanting richer lines (e.g. relevance_score)
# set `include_fields` on the LogNotifierSpec.
_DEFAULT_LOG_FIELDS: list[str] = ["title", "url"]


def _render_text(batch: HitBatch, spec: LogNotifierSpec) -> str:
    # Respects the operator's `include_fields` (mirrors how WebhookNotifier
    # treats the same field). When unset, defaults to title + url so the
    # baseline dev-tick line stays readable.
    fields = list(spec.include_fields) if spec.include_fields else _DEFAULT_LOG_FIELDS
    payload = build_payload(batch, include_fields=fields)
    lines = [f"{spec.prefix} {payload['listener_name']} | {payload['total_hits']} hits | {_period(batch)}"]
    for source, hits in payload["hits_by_source"].items():
        lines.append(f"  {source} ({len(hits)})")
        for hit in hits:
            lines.append(f"    - {_render_hit_line(hit, fields)}")
    return "\n".join(lines) + "\n"


def _render_hit_line(hit: dict, fields: list[str]) -> str:
    """One line per hit. Title + url get positional treatment so the
    baseline output reads `title | url`; any additional fields the
    operator pulled in via include_fields tail on as `key=value`."""
    parts: list[str] = []
    title_used = False
    url_used = False
    if "title" in fields and "title" in hit:
        parts.append(_oneline(str(hit["title"])))
        title_used = True
    if "url" in fields and "url" in hit:
        parts.append(str(hit["url"]))
        url_used = True
    for k in fields:
        if (k == "title" and title_used) or (k == "url" and url_used):
            continue
        if k in hit:
            parts.append(f"{k}={hit[k]}")
    return " | ".join(parts)


def _period(batch: HitBatch) -> str:
    end = batch.period_end.strftime("%Y-%m-%d %H:%M")
    if batch.period_start is None:
        return f"instant @ {end} UTC"
    return f"{batch.period_start.strftime('%Y-%m-%d %H:%M')} to {end} UTC"


def _oneline(text: str) -> str:
    # Collapse any newlines / whitespace runs so one hit stays on one line.
    # Not truncation: the full title is preserved, just flattened.
    return " ".join(text.split())
