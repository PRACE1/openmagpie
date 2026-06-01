"""Server-side Feed-config POLICY (the Django/settings-coupled guards
that can't live in the pure shared `openmagpie-schema` package).

`enforce_policy` runs at the validation seams (serializer -> 400;
service). Idempotent; pure predicates.

Guards on the feed config:
  - retention_days in [1, 365] (0/negative would prune everything;
    unbounded would grow the item log forever).

Per-source `last_event_at` defaulting + future-watermark rejection
moved to `default_and_enforce_source_watermark`, called from the
Source create / set paths (rows on a different model than the config).

Raises `PolicyError` (ValueError); callers map it to a 400.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from feeds.configs import FeedConfig
from openmagpie_schema.configs import RssSourceSpec, SourceSpec

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


def _enforce_source_url_safety(spec: SourceSpec) -> None:
    """settings-driven SSRF policy on Source URLs ; structural URL
    checks (scheme + host present) already ran in the schema
    (`RssSourceSpec._validate_url_structural`). This adds the operational
    private-IP gate at the create seam so an IP-literal URL pointing at
    a loopback / metadata / link-local address is rejected as a 400
    instead of fetched at poll time.

    DNS resolution + re-validation on redirect happens at the connector
    side (poll time) because (a) DNS can change between create and
    poll and (b) public hostnames can 302 to internal targets. This
    seam catches the "operator pasted a private IP" case ; the
    connector catches the DNS / redirect cases."""
    if not isinstance(spec, RssSourceSpec):
        return
    if not settings.SOURCE_BLOCK_PRIVATE_IPS:
        return
    parsed = urlparse(spec.url)
    if not parsed.hostname:
        return  # schema validator catches this
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # hostname, not an IP literal ; connector resolves at poll time
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise PolicyError(f"SOURCE_BLOCK_PRIVATE_IPS is set; URL host resolves to blocked IP {ip}")


def enforce_source_spec_safety(specs: list[SourceSpec]) -> None:
    """Apply server-policy URL safety guards to every spec. Called from
    `SourceService.set_sources` so a CLI / API create or replace fails
    loud with a 400 instead of reaching the connector."""
    for spec in specs:
        _enforce_source_url_safety(spec)


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
