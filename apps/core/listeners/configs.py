"""Typed config schemas for Listener.data, keyed by Listener.kind.

Each listener kind has its own Pydantic class describing the shape of its
`data` JSON blob. Only the queryable common fields live on the Listener model;
everything else lives here, typed.
"""

import ipaddress
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import urlparse, urlsplit

from django.conf import settings
from django.utils import timezone
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


def _is_redacted_url(url: str) -> bool:
    """True if `url` is the placeholder `redacted()` emits, i.e. the
    operator left it masked on an edit (didn't change it). The redacted
    form always ends `/***`; bare `***` covers an unparseable original."""
    return url == REDACTED or url.endswith("/***")


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

    @field_validator("last_event_at")
    @classmethod
    def _reject_future(cls, value: datetime | None) -> datetime | None:
        # A future watermark means "yield nothing until wall-clock passes
        # this", i.e. it silently disables the stream with no error - a
        # real footgun the template's date example invites. (We do NOT
        # bound how far *back* it can go: the only connector is API-capped
        # at ~1000 items regardless, so an old timestamp is already
        # bounded; an arbitrary age horizon would be a magic number with
        # no real connector behind it.)
        if value is None:
            return value
        now = timezone.now()
        v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
        if v > now:
            raise ValueError(
                f"last_event_at is in the future ({value.isoformat()}); "
                "a future watermark silently disables the stream until then"
            )
        return value


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
    """Common base for notifier specs. Owns both halves of the secret
    contract so they live next to the schema (single source of truth):
    `redacted()` masks secrets for output; `restore_secrets_from(prior)`
    is its inverse for edit round-trip - a value still equal to the
    redaction sentinel means "unchanged", so keep the prior real value.
    Defaults: nothing secret, return self. Secret-bearing specs override
    BOTH (so a new one can't implement masking without round-trip)."""

    def redacted(self) -> "NotifierSpecBase":
        return self

    def restore_secrets_from(self, prior: "NotifierSpecBase | None") -> "NotifierSpecBase":
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
    include_fields: list[str] = []  # empty = all observation fields except scoping; explicit list = whitelist

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"webhook URL scheme must be http or https, got {parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError(f"webhook URL missing host: {value!r}")
        if settings.WEBHOOK_REQUIRE_HTTPS and parsed.scheme != "https":
            raise ValueError(f"WEBHOOK_REQUIRE_HTTPS is set; webhook URL must be https (got {parsed.scheme!r})")
        # If the host is a literal IP, refuse blocked ranges at save time.
        # Hostnames are resolved + checked at send time (TOCTOU caveat noted in the notifier).
        if settings.WEBHOOK_BLOCK_PRIVATE_IPS and parsed.hostname:
            try:
                ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass  # not an IP literal; skip
            else:
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                    raise ValueError(f"WEBHOOK_BLOCK_PRIVATE_IPS is set; URL host resolves to blocked IP {ip}")
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

    def restore_secrets_from(self, prior: "NotifierSpecBase | None") -> "WebhookNotifierSpec":
        """Inverse of `redacted()` for edit round-trip: a value still
        equal to the redaction sentinel means the operator left it
        masked, so keep the prior real value; a changed value is taken
        as-is. `prior` is the already-paired prior notifier for this
        slot (caller does the O(1) pairing) - no lookup here. Header
        check is O(1) dict membership."""
        if not isinstance(prior, WebhookNotifierSpec):
            return self  # slot kind changed: nothing to restore
        return self.model_copy(
            update={
                "url": prior.url if _is_redacted_url(self.url) else self.url,
                "headers": {
                    name: prior.headers[name] if value == REDACTED and name in prior.headers else value
                    for name, value in self.headers.items()
                },
            }
        )


class LogNotifierSpec(NotifierSpecBase):
    """Writes the batch to stdout. For dev / verification. No secrets,
    so it inherits the no-op `redacted` from NotifierSpecBase."""

    kind: Literal["log"] = "log"
    prefix: str = "[hit]"
    include_fields: list[str] = []  # same semantics as WebhookNotifierSpec


NotifierSpec = Annotated[
    WebhookNotifierSpec | LogNotifierSpec,
    Field(discriminator="kind"),
]


# ── Listener kind configs ─────────────────────────────────────────────────


class ListenerConfigSummary(BaseModel):
    """Display-only projection of a listener config for the CLI preview.

    Built server-side from the typed config (the only place that knows
    the schema), so the CLI prints it without parsing the `data` blob -
    no shadow schema on the client. Strings are presentation, not a
    contract; the CLI owns final layout."""

    streams: list[str] = []
    notifiers: list[str] = []
    engine: str = ""


class ListenerConfig(BaseModel):
    """Base for every listener-kind config.

    Declares the read-path contract every kind MUST implement:
    `redacted_dump()` (the create-path `model_validate().model_dump()`
    counterpart) and `summary()` (the CLI display projection). Schema
    knowledge lives here, on the typed config, never on the client.

    These are NOT given working defaults on purpose. A silent default
    would be a footgun: an empty `summary()` shows a blank preview with
    no signal, and a `model_dump()` `redacted_dump()` default would leak
    a new kind's secrets UNREDACTED. A kind that forgets to implement
    them must fail loudly here, not ship a silent hole."""

    def redacted_dump(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement redacted_dump() (no safe default: the fallback would leak secrets)"
        )

    def summary(self) -> ListenerConfigSummary:
        raise NotImplementedError(f"{type(self).__name__} must implement summary()")

    def merge_preserving(self, prior: "ListenerConfig") -> "ListenerConfig":
        """Edit round-trip: return self with state that must NOT reset on
        an edit carried over from `prior` - per-stream poll watermarks
        and redacted secrets the operator left masked. No safe default:
        a silent passthrough would reset every watermark (re-poll/re-
        judge history) and corrupt every secret to `***`. A kind must
        implement this consciously."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement merge_preserving() "
            "(no safe default: would reset watermarks and corrupt secrets)"
        )


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
        scrubbed = self.model_copy(update={"notifiers": [n.redacted() for n in self.notifiers]})
        return scrubbed.model_dump(mode="json")

    def summary(self) -> ListenerConfigSummary:
        """Each stream rendered by its own `spec.display()` (so a new
        connector contributes its label without touching this), notifiers
        by kind, engine as `kind | model`. No secrets (URLs/headers never
        appear here)."""
        engine = f"{self.engine.kind} | {self.engine.model}" if self.engine.model else self.engine.kind
        return ListenerConfigSummary(
            streams=[w.spec.display() for w in self.streams],
            notifiers=[n.kind for n in self.notifiers],
            engine=engine,
        )

    def merge_preserving(self, prior: "ListenerConfig") -> "SemanticListenerConfig":
        """Carry forward, from `prior`, the config state an edit must not
        reset (id/created_at/poll-state COLUMNS are the service's job,
        not this - this is config-blob only):

        - per-stream `last_event_at`, keyed by spec identity, so editing
          unrelated fields doesn't cold-start a stream and re-judge
          history;
        - redacted secrets the operator left as `***` (the GET we
          round-tripped through is redacted, so unchanged secrets come
          back masked).

        Lookups precomputed once: a spec->watermark dict (O(n) build,
        O(1) per stream) and prior notifiers indexed by position (O(1)).
        No nested scans.
        """
        prior_streams = getattr(prior, "streams", [])
        prior_notifiers = getattr(prior, "notifiers", [])

        watermarks = {w.spec.model_dump_json(): w.last_event_at for w in prior_streams}
        streams = [
            w.model_copy(update={"last_event_at": watermarks[key]})
            if (key := w.spec.model_dump_json()) in watermarks
            else w  # new stream (no prior match): keep submitted (None=cold-start)
            for w in self.streams
        ]
        notifiers = [
            n.restore_secrets_from(prior_notifiers[i] if i < len(prior_notifiers) else None)
            for i, n in enumerate(self.notifiers)
        ]
        return self.model_copy(update={"streams": streams, "notifiers": notifiers})
