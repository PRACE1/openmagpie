"""Server-side listener-config POLICY (the Django/settings-coupled
guards that can't live in the pure shared `openmagpie-schema` package).

These were `field_validator`s on the config models; they moved here
intact when the models were extracted to the shared package. They are
NOT lost - `enforce_policy` runs at the same seams the validators
effectively did:

  - `ListenerCreateSerializer.validate` (HTTP boundary -> 400)
  - `ListenerService.build` / `build_update` (the service seam)

`enforce_policy` is idempotent (safe to run at both): the engine-kind
fill is a no-op once set; the checks are pure predicates.

Guards (each preserved verbatim from the old validators):
  1. engine kind: empty -> settings.ENGINE_DEFAULT_KIND; then it MUST
     be registered in engine.registry (else a typo cold-starts fine
     then wedges the scheduler on the first warm poll).
  2. no future `last_event_at` (a future watermark silently disables
     the stream until wall-clock passes it).
  3. webhook SSRF: settings.WEBHOOK_REQUIRE_HTTPS /
     WEBHOOK_BLOCK_PRIVATE_IPS (the structural http/https+host check
     stays in the pure model).

Raises `PolicyError` (ValueError) on violation; callers map it to a
400 (HTTP) or let it propagate (mgmt-command seam).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from openmagpie_schema.configs import (
    ListenerConfig,
    WebhookNotifierSpec,
)


class PolicyError(ValueError):
    """A listener config violates server policy. Mapped to a 400 at the
    HTTP boundary; the message is operator-facing."""


def _enforce_engine(config: ListenerConfig) -> ListenerConfig:
    """Fill an empty engine kind from settings, then require it to be a
    registered engine. Returns config (engine normalized if it was
    empty)."""
    engine = getattr(config, "engine", None)
    if engine is None:
        return config

    kind = engine.kind
    if not kind:
        kind = settings.ENGINE_DEFAULT_KIND
        config = config.model_copy(update={"engine": engine.model_copy(update={"kind": kind})})

    # Lazy import: engine.registry instantiates engines from settings at
    # import; importing at call time avoids an app-load cycle.
    from engine import registry as engine_registry

    valid = engine_registry.kinds()
    if kind not in valid:
        raise PolicyError(f"unknown engine kind {kind!r}; expected one of {valid}")
    return config


def _enforce_watermarks(config: ListenerConfig) -> None:
    """No future per-stream `last_event_at` (silently disables the
    stream until wall-clock passes it)."""
    now = timezone.now()
    for watch in getattr(config, "streams", []):
        value = watch.last_event_at
        if value is None:
            continue
        # Pydantic parses offset-aware ISO into an aware datetime (the
        # normal path). A *naive* value is assumed UTC (now.tzinfo is
        # UTC under USE_TZ) - a naive local-time input would be read as
        # UTC. Acceptable: the API contract is ISO-8601 with offset.
        v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
        if v > now:
            raise PolicyError(
                f"last_event_at is in the future ({value.isoformat()}); "
                "a future watermark silently disables the stream until then"
            )


def _enforce_webhooks(config: ListenerConfig) -> None:
    """settings-driven SSRF policy on webhook URLs. The pure model
    already enforced http/https scheme + host present."""
    for notifier in getattr(config, "notifiers", []):
        if not isinstance(notifier, WebhookNotifierSpec):
            continue
        parsed = urlparse(notifier.url)
        if settings.WEBHOOK_REQUIRE_HTTPS and parsed.scheme != "https":
            raise PolicyError(f"WEBHOOK_REQUIRE_HTTPS is set; webhook URL must be https (got {parsed.scheme!r})")
        if settings.WEBHOOK_BLOCK_PRIVATE_IPS and parsed.hostname:
            try:
                ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                continue  # not an IP literal; resolved+checked at send time
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                raise PolicyError(f"WEBHOOK_BLOCK_PRIVATE_IPS is set; URL host resolves to blocked IP {ip}")


def enforce_policy(config: ListenerConfig) -> ListenerConfig:
    """Apply every server policy guard; return the (engine-normalized)
    config or raise `PolicyError`. Idempotent."""
    config = _enforce_engine(config)
    _enforce_watermarks(config)
    _enforce_webhooks(config)
    return config
