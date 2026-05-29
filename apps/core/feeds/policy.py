"""Server-side Feed-config POLICY (the Django/settings-coupled guards
that can't live in the pure shared `openmagpie-schema` package).

Mirrors `listeners.policy`. `enforce_policy` runs at the validation
seams (serializer -> 400; service). Idempotent; pure predicates.

Guards on the feed config:
  - retention_days in [1, 365] (0/negative would prune everything;
    unbounded would grow the item log forever).

Per-source `last_event_at` defaulting + future-watermark rejection
moved to `default_and_enforce_source_watermark`, called from the
Source create / set paths (rows on a different model than the config).

Raises `PolicyError` (ValueError); callers map it to a 400.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from feeds.configs import FeedConfig

RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 365


class PolicyError(ValueError):
    """A feed config violates server policy. Mapped to a 400 at the HTTP
    boundary; the message is operator-facing."""


def default_and_enforce_source_watermark(value: datetime | None) -> datetime:
    """Fill a missing per-source `last_event_at` with wall-clock now;
    reject a future watermark. Returned value is used as-is on the new
    Source row, making `last_event_at is None` impossible post-validation
    so the poller can treat it as a hard invariant. Operators who want
    a backfill window pass an explicit past datetime."""
    now = timezone.now()
    if value is None:
        return now
    v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
    if v > now:
        raise PolicyError(
            f"last_event_at is in the future ({value.isoformat()}); "
            "a future watermark silently disables the source until then"
        )
    return value


def _enforce_retention(config: FeedConfig) -> None:
    """retention_days must be a sane bound."""
    days = getattr(config, "retention_days", None)
    if days is None:
        return
    if not (RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS):
        raise PolicyError(f"retention_days must be between {RETENTION_MIN_DAYS} and {RETENTION_MAX_DAYS} (got {days})")


def enforce_policy(config: FeedConfig) -> FeedConfig:
    """Apply every server policy guard on the feed config; return the
    config or raise `PolicyError`. Idempotent. Per-source watermark
    policy runs on the Source create/set paths, not here."""
    _enforce_retention(config)
    return config
