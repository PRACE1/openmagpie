"""WebhookNotifier, POSTs a JSON payload to a configured URL.

Two-layer SSRF protection:
  1. Save-time: `WebhookNotifierSpec` validates the URL scheme (http/https).
  2. Send-time: `_check_destination` re-checks scheme + (optionally) resolves
     the hostname and blocks private / loopback / link-local / reserved IPs.

The runtime gates are settings-driven so single-tenant self-hosters can
hit internal services (e.g. OpenClaw on a private IP); multi-tenant /
public deployments should set:
  WEBHOOK_REQUIRE_HTTPS=true
  WEBHOOK_BLOCK_PRIVATE_IPS=true
"""

import ipaddress
import socket
import time
from urllib.parse import urlparse

import httpx
from django.conf import settings
from listeners.configs import WebhookNotifierSpec
from notifications.services.batching import build_payload

from .base import HitBatch, NotificationResult


def _check_destination(url: str) -> str | None:
    """Return an error string if the URL should be blocked, None if allowed."""
    parsed = urlparse(url)
    if settings.WEBHOOK_REQUIRE_HTTPS and parsed.scheme != "https":
        return f"WEBHOOK_REQUIRE_HTTPS is set; scheme={parsed.scheme!r}"

    if settings.WEBHOOK_BLOCK_PRIVATE_IPS:
        host = parsed.hostname
        if not host:
            return f"URL missing hostname: {url!r}"
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            return f"DNS resolution failed for {host!r}: {exc}"
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
            ):
                return f"hostname {host!r} resolves to blocked IP {ip}"
    return None


class WebhookNotifier:
    kind = "webhook"

    def deliver(self, batch: HitBatch, spec: WebhookNotifierSpec) -> NotificationResult:
        blocked = _check_destination(spec.url)
        if blocked is not None:
            return NotificationResult(
                notifier_kind=self.kind,
                delivered=False,
                latency_ms=0,
                error=f"blocked: {blocked}",
            )

        payload = build_payload(batch, include_fields=spec.include_fields)
        started = time.perf_counter()
        try:
            response = httpx.post(
                spec.url, json=payload, headers=spec.headers, timeout=30.0
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return NotificationResult(
                notifier_kind=self.kind,
                delivered=False,
                latency_ms=elapsed,
                error=str(exc),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        return NotificationResult(
            notifier_kind=self.kind, delivered=True, latency_ms=elapsed
        )
