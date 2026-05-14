"""Typed config schemas for Listener.data, keyed by Listener.kind.

Each listener kind has its own Pydantic class describing the shape of its
`data` JSON blob. Only the queryable common fields live on the Listener model;
everything else lives here, typed.
"""

import ipaddress
from datetime import datetime
from typing import Annotated, ClassVar, Literal, Union
from urllib.parse import urlparse

from django.conf import settings
from pydantic import BaseModel, Field, field_validator


# ── Stream specs (discriminated union over kind) ──────────────────────────
#
# Each connector defines its own immutable identity shape. Add a new variant
# when adding a connector; identity fields are named for what they actually
# are (`subreddit`, `repo`, `feed_url`), not a generic `slug`. Per-stream
# poll state (`last_event_at`) lives on StreamWatch, not on the spec.


class RedditSubredditStreamSpec(BaseModel):
    """Identity of one subreddit feed. Bound to RedditSubRedditConnector."""

    kind: Literal["reddit_subreddit"] = "reddit_subreddit"
    subreddit: str

    def display(self) -> str:
        return f"r/{self.subreddit}"


StreamSpec = Annotated[
    RedditSubredditStreamSpec,
    Field(discriminator="kind"),
]


class StreamWatch(BaseModel):
    """A stream this Listener is watching, identity + per-stream poll state."""

    spec: StreamSpec
    last_event_at: datetime | None = None  # high-water mark for incremental polling


class EngineSpec(BaseModel):
    """Which engine + model this listener uses to judge relevance.

    `kind` must be registered in `engine.registry`. `model` is informational,
    the actual model is currently bound at registry-init time from settings
    (e.g. `OLLAMA_MODEL`). Wire `model` through to the engine call if/when
    per-listener model override becomes a real need.
    """

    kind: str
    model: str = ""


def _default_engine_spec() -> EngineSpec:
    """Build the fallback engine spec from settings, avoids hardcoding an
    engine kind in the listeners app."""
    return EngineSpec(kind=settings.ENGINE_DEFAULT_KIND)


# ── Notifier specs (discriminated union over kind) ────────────────────────


class WebhookNotifierSpec(BaseModel):
    """POSTs a JSON payload to a configured URL.

    Security notes (single-tenant self-host assumption):
      - URL scheme is validated to http/https here. Runtime delivery additionally
        enforces `settings.WEBHOOK_REQUIRE_HTTPS` and `settings.WEBHOOK_BLOCK_PRIVATE_IPS`
       , see notifications/notifiers/webhook.py.
      - `headers` is forwarded verbatim. Fine when the operator controls the listener
        config. If this is reused in a multi-tenant deployment, treat headers as
        untrusted input (allowlist or strip Authorization/Cookie, etc.).
    """

    kind: Literal["webhook"] = "webhook"
    url: str
    headers: dict[str, str] = {}
    include_fields: list[
        str
    ] = []  # empty = all observation fields except scoping; explicit list = whitelist

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"webhook URL scheme must be http or https, got {parsed.scheme!r}"
            )
        if not parsed.netloc:
            raise ValueError(f"webhook URL missing host: {value!r}")
        if settings.WEBHOOK_REQUIRE_HTTPS and parsed.scheme != "https":
            raise ValueError(
                f"WEBHOOK_REQUIRE_HTTPS is set; webhook URL must be https (got {parsed.scheme!r})"
            )
        # If the host is a literal IP, refuse blocked ranges at save time.
        # Hostnames are resolved + checked at send time (TOCTOU caveat noted in the notifier).
        if settings.WEBHOOK_BLOCK_PRIVATE_IPS and parsed.hostname:
            try:
                ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass  # not an IP literal; skip
            else:
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                ):
                    raise ValueError(
                        f"WEBHOOK_BLOCK_PRIVATE_IPS is set; URL host resolves to blocked IP {ip}"
                    )
        return value


class LogNotifierSpec(BaseModel):
    """Writes the batch to stdout. For dev / verification."""

    kind: Literal["log"] = "log"
    prefix: str = "[hit]"
    include_fields: list[str] = []  # same semantics as WebhookNotifierSpec


NotifierSpec = Annotated[
    Union[WebhookNotifierSpec, LogNotifierSpec],
    Field(discriminator="kind"),
]


# ── Listener kind configs ─────────────────────────────────────────────────


class SemanticListenerConfig(BaseModel):
    """Schema for Listener.data when Listener.kind == 'semantic'."""

    LISTENER_KIND: ClassVar[str] = "semantic"

    streams: list[StreamWatch] = []
    refined_instructions: str = ""
    engine: EngineSpec = Field(default_factory=_default_engine_spec)
    # An Observation is a hit when engine.judge(...).score >= hit_threshold.
    # 0.8 as a Pareto default, most of the value with little noise. Dial down
    # to widen the net (more matches, more LLM-questionable hits), or up to
    # only catch the obvious matches.
    hit_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    notifiers: list[NotifierSpec] = []
    # only consumed when listener.delivery_mode == Listener.DeliveryMode.DIGEST
    digest_interval_seconds: int = 3600

    model_config = {"extra": "ignore"}
