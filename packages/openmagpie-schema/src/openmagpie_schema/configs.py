"""Pure typed config schemas for a Listener's `data` blob, keyed by kind.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). It carries only *shape* + pure transforms (redact / restore /
merge / summary). The Django/settings-coupled *policy* (engine-kind is
registered, no future watermark, webhook SSRF/https rules, default
engine kind) is NOT here - it lives in `core` (`listeners.policy`) and
runs at the server's validation seam. Splitting shape from policy is
what lets this module be a dependency-free shared package; the guards
are preserved, just relocated.
"""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import urlsplit

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
    """A stream this Listener is watching, identity + per-stream poll state.

    The "no future watermark" guard is server policy (it needs the
    wall clock); see `core` `listeners.policy`."""

    spec: StreamSpec
    last_event_at: datetime | None = None  # high-water mark for incremental polling


class EngineSpec(BaseModel):
    """Which engine + model this listener uses to judge relevance.

    `kind == ""` means "use the server default" - the server fills it
    from settings and rejects an unregistered kind (policy; the pure
    package can't know the registry). `model` is informational.
    """

    kind: str = ""
    model: str = ""


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

    Only the *structural* URL check (http/https scheme, host present) is
    here - it's pure. The settings-dependent SSRF policy
    (WEBHOOK_REQUIRE_HTTPS, WEBHOOK_BLOCK_PRIVATE_IPS) is server policy;
    see `core` `listeners.policy`. `headers` is forwarded verbatim
    (single-tenant self-host assumption).
    """

    kind: Literal["webhook"] = "webhook"
    url: str
    headers: dict[str, str] = {}
    include_fields: list[str] = []  # empty = all observation fields except scoping

    @field_validator("url")
    @classmethod
    def _validate_url_structural(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"webhook URL scheme must be http or https, got {parts.scheme!r}")
        if not parts.netloc:
            raise ValueError(f"webhook URL missing host: {value!r}")
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
        slot (caller does the O(1) pairing) - no lookup here."""
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
    `redacted_dump()` and `summary()`. NOT given working defaults: a
    silent `summary()` shows a blank preview, and a `model_dump()`
    `redacted_dump()` default would leak a new kind's secrets. Fail
    loudly here, not ship a silent hole."""

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
        a silent passthrough would reset every watermark and corrupt
        every secret to `***`."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement merge_preserving() "
            "(no safe default: would reset watermarks and corrupt secrets)"
        )


class SemanticListenerConfig(ListenerConfig):
    """Schema for Listener.data when Listener.kind == 'semantic'."""

    LISTENER_KIND: ClassVar[str] = "semantic"

    streams: list[StreamWatch] = []
    refined_instructions: str = ""
    # default = EngineSpec(kind=""); the server fills the real default
    # kind from settings + validates it (policy).
    engine: EngineSpec = Field(default_factory=EngineSpec)
    # An Observation is a hit when engine.judge(...).score >= hit_threshold.
    # 0.8 Pareto default: most value, little noise.
    hit_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    notifiers: list[NotifierSpec] = []
    # only consumed when listener.delivery_mode == DeliveryMode.DIGEST
    digest_interval_seconds: int = 3600

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """Redact per notifier spec, then dump. Each spec masks its own
        secrets (see NotifierSpecBase.redacted), so adding a new
        secret-bearing notifier needs no change here."""
        scrubbed = self.model_copy(update={"notifiers": [n.redacted() for n in self.notifiers]})
        return scrubbed.model_dump(mode="json")

    def summary(self) -> ListenerConfigSummary:
        """Each stream rendered by its own `spec.display()`, notifiers by
        kind, engine as `kind | model`. No secrets."""
        engine = f"{self.engine.kind} | {self.engine.model}" if self.engine.model else self.engine.kind
        return ListenerConfigSummary(
            streams=[w.spec.display() for w in self.streams],
            notifiers=[n.kind for n in self.notifiers],
            engine=engine,
        )

    def merge_preserving(self, prior: "ListenerConfig") -> "SemanticListenerConfig":
        """Carry forward, from `prior`, the config state an edit must not
        reset (id/created_at/poll-state COLUMNS are the service's job):

        - per-stream `last_event_at`, keyed by spec identity, so editing
          unrelated fields doesn't cold-start a stream;
        - redacted secrets the operator left as `***`.

        Lookups precomputed once (O(n) build, O(1) per item)."""
        prior_streams = getattr(prior, "streams", [])
        prior_notifiers = getattr(prior, "notifiers", [])

        watermarks = {w.spec.model_dump_json(): w.last_event_at for w in prior_streams}
        streams = [
            w.model_copy(update={"last_event_at": watermarks[key]})
            if (key := w.spec.model_dump_json()) in watermarks
            else w  # new stream (no prior match): keep submitted (None=cold-start)
            for w in self.streams
        ]
        # Notifiers have no non-secret identity to key on (a webhook's
        # url + headers ARE the secret), so a masked secret is restored
        # by position WITHIN its kind: the i-th submitted webhook pairs
        # with the i-th prior webhook. Kind-filtering (not raw list
        # index) means adding/removing a non-webhook notifier - the
        # common edit - can't shift a webhook onto a non-webhook slot
        # and silently drop its secret. If a masked secret still can't
        # be matched (webhook count changed, or a reorder the mask
        # hides), REFUSE: persisting '***' as the live URL/header is
        # silent secret loss, the worst outcome. The operator re-enters
        # the secret explicitly (server maps this to a 400).
        prior_webhooks = [n for n in prior_notifiers if isinstance(n, WebhookNotifierSpec)]
        restored = []
        webhook_i = 0
        for n in self.notifiers:
            if isinstance(n, WebhookNotifierSpec):
                paired = prior_webhooks[webhook_i] if webhook_i < len(prior_webhooks) else None
                webhook_i += 1
                n = n.restore_secrets_from(paired)
                if _is_redacted_url(n.url) or any(v == REDACTED for v in n.headers.values()):
                    raise ValueError(
                        "a webhook secret was left masked ('***') but could not be matched "
                        "to a prior webhook to restore it (the notifier list changed); "
                        "re-enter the webhook URL and any secret header values explicitly"
                    )
            restored.append(n)
        return self.model_copy(update={"streams": streams, "notifiers": restored})
