"""Server-side Feed-config POLICY (the Django/settings-coupled guards
that can't live in the pure shared `openmagpie-schema` package).

Mirrors `listeners.policy`. `enforce_policy` runs at the validation
seams (serializer -> 400; service). Idempotent; pure predicates.

Guards:
  1. no future per-stream `last_event_at` (a future watermark silently
     disables the stream until wall-clock passes it).
  2. retention_days in [1, 365] (0/negative would prune everything;
     unbounded would grow the item log forever).

Raises `PolicyError` (ValueError); callers map it to a 400.
"""

from __future__ import annotations

from django.utils import timezone

from feeds.configs import FeedConfig

RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 365


class PolicyError(ValueError):
    """A feed config violates server policy. Mapped to a 400 at the HTTP
    boundary; the message is operator-facing."""


def _enforce_watermarks(config: FeedConfig) -> None:
    """No future per-stream `last_event_at`."""
    now = timezone.now()
    for watch in getattr(config, "streams", []):
        value = watch.last_event_at
        if value is None:
            continue
        v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
        if v > now:
            raise PolicyError(
                f"last_event_at is in the future ({value.isoformat()}); "
                "a future watermark silently disables the stream until then"
            )


def _enforce_retention(config: FeedConfig) -> None:
    """retention_days must be a sane bound."""
    days = getattr(config, "retention_days", None)
    if days is None:
        return
    if not (RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS):
        raise PolicyError(f"retention_days must be between {RETENTION_MIN_DAYS} and {RETENTION_MAX_DAYS} (got {days})")


def enforce_policy(config: FeedConfig) -> FeedConfig:
    """Apply every server policy guard; return the config or raise
    `PolicyError`. Idempotent.

    Duck-types the config (getattr) because only CuratedFeedConfig exists
    today; when a 2nd kind lands, replace with an explicit structural
    contract on FeedConfig (matches the listeners.policy stance)."""
    _enforce_watermarks(config)
    _enforce_retention(config)
    return config
