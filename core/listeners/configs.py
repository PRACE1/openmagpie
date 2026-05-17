"""Typed config schemas for Listener.data, keyed by Listener.kind.

Each listener kind has its own Pydantic class describing the shape of its
`data` JSON blob. Only the queryable common fields live on the Listener model;
everything else lives here, typed.
"""

import ipaddress
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Union
from urllib.parse import urlparse, urlsplit

from django.conf import settings
from pydantic import BaseModel, Field, field_validator

# Replacement for any secret value in a redacted dump.
REDACTED = "***"


def _redact_webhook_url(url: str) -> str:
    """Strip the secret-bearing parts of a webhook URL, keep the host.

    Slack/Discord put the token in the path, so dropping only the query
    would still leak. Keep `scheme://host[:port]` for recognition and
    collapse everything after the authority to `/***`. A URL that won't
    parse is replaced wholesale rather than risking a partial leak.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    if not parts.scheme or not parts.hostname:
        return REDACTED
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}/***"


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

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        # Reject an unregistered engine kind at create time. Without this a
        # typo passes validation, cold-start succeeds, then the first warm
        # poll raises KeyError out of engine.registry.get and (being
        # non-recoverable) aborts the whole scheduler run. Lazy import:
        # engine.registry instantiates engines from settings at import, so
        # importing it at validation time avoids an app-load import cycle.
        from engine import registry as engine_registry

        valid = engine_registry.kinds()
        if value not in valid:
            raise ValueError(f"unknown engine kind {value!r}; expected one of {valid}")
        return value


def _default_engine_spec() -> EngineSpec:
    """Build the fallback engine spec from settings, avoids hardcoding an
    engine kind in the listeners app."""
    return EngineSpec(kind=settings.ENGINE_DEFAULT_KIND)


# ── Notifier specs (discriminated union over kind) ────────────────────────


class NotifierSpecBase(BaseModel):
    """Common base for notifier specs. Owns the redaction contract so it
    lives next to the schema it redacts (single source of truth; symmetric
    with how create validates through this same typed config). Default:
    nothing secret, return self. Specs with secrets override `redacted`."""

    def redacted(self) -> "NotifierSpecBase":
        return self


class WebhookNotifierSpec(NotifierSpecBase):
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

    def redacted(self) -> "WebhookNotifierSpec":
        """Mask both secret carriers: header values and the URL (Slack/
        Discord/HMAC tokens live in the path/query). Header names are
        kept so an operator can see which headers are set."""
        return self.model_copy(
            update={
                "url": _redact_webhook_url(self.url),
                "headers": dict.fromkeys(self.headers, REDACTED),
            }
        )


class LogNotifierSpec(NotifierSpecBase):
    """Writes the batch to stdout. For dev / verification. No secrets,
    so it inherits the no-op `redacted` from NotifierSpecBase."""

    kind: Literal["log"] = "log"
    prefix: str = "[hit]"
    include_fields: list[str] = []  # same semantics as WebhookNotifierSpec


NotifierSpec = Annotated[
    Union[WebhookNotifierSpec, LogNotifierSpec],
    Field(discriminator="kind"),
]


# ── Listener kind configs ─────────────────────────────────────────────────


class ListenerConfig(BaseModel):
    """Base for every listener-kind config.

    Owns `redacted_dump()`: the read-path counterpart to the create-path
    `model_validate(...).model_dump(...)`. The serializer calls this
    instead of hand-walking the raw blob, so what is secret is declared
    once, on the typed config, and never drifts from the schema. Default:
    nothing secret. Kinds with secret-bearing sub-specs override."""

    def redacted_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SemanticListenerConfig(ListenerConfig):
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

    def redacted_dump(self) -> dict[str, Any]:
        """Redact per notifier spec, then dump. Each spec masks its own
        secrets (see NotifierSpecBase.redacted), so adding a new
        secret-bearing notifier needs no change here."""
        scrubbed = self.model_copy(
            update={"notifiers": [n.redacted() for n in self.notifiers]}
        )
        return scrubbed.model_dump(mode="json")
