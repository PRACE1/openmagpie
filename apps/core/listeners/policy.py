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

Guards:
  1. engine kind: empty -> settings.ENGINE_DEFAULT_KIND; then it MUST
     be registered in engine.registry (else a typo cold-starts fine
     then wedges the scheduler on the first warm poll).
  2. webhook SSRF: settings.WEBHOOK_REQUIRE_HTTPS /
     WEBHOOK_BLOCK_PRIVATE_IPS (the structural http/https+host check
     stays in the pure model).

(The no-future-watermark guard moved to feeds.policy: streams are owned
by the Feed now, not the Listener.)

Raises `PolicyError` (ValueError) on violation; callers map it to a
400 (HTTP) or let it propagate (mgmt-command seam).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from django.conf import settings

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
    config or raise `PolicyError`. Idempotent.

    The guards duck-type the config (`getattr(config, "engine"/
    "notifiers", default)`) because `ListenerConfig` is the abstract base
    and only `SemanticListenerConfig` exists today. A future kind that
    omits or renames these fields would silently skip the guard, not
    fail loud. When a 2nd kind lands, replace the getattrs with an
    explicit structural contract on `ListenerConfig` (e.g. abstract
    accessors) so a missing guard is a hard error, matching the base
    class's NotImplementedError stance for the read path.

    (No watermark guard here anymore: listeners don't own streams - the
    Feed does, and that guard lives in feeds.policy.)"""
    config = _enforce_engine(config)
    _enforce_webhooks(config)
    return config
