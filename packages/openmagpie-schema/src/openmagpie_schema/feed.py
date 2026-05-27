"""Pure typed config + wire schemas for a Feed.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). A Feed is the curated/discovered set of streams a Listener
subscribes to: it owns the stream set + per-stream watermarks and (in
core) the poll loop + a readable item log. This module carries only
*shape* + pure transforms; the Django/settings-coupled *policy* (no
future watermark, retention bounds) lives in core `feeds.policy`.

Mirrors `configs.py` (config base + concrete kind) and `wire.py`
(response envelope quartet) for Listener, deliberately, so the two
primitives are structurally identical.
"""

from typing import Any, ClassVar

from pydantic import BaseModel

from .configs import StreamWatch
from .wire import ConfigBlob

# ── Config (write-path `data` blob, keyed by kind) ────────────────────────


class FeedConfigSummary(BaseModel):
    """Display-only projection of a feed config for the CLI preview.

    Built server-side from the typed config (the only place that knows
    the schema) so the CLI prints it without parsing the `data` blob."""

    streams: list[str] = []
    stream_count: int = 0


class FeedConfig(BaseModel):
    """Base for every feed-kind config.

    Declares the contract every kind MUST implement; no working defaults
    (a silent default would show a blank preview or reset watermarks).
    Mirrors `ListenerConfig`."""

    def redacted_dump(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement redacted_dump() (no safe default: the fallback could leak secrets)"
        )

    def summary(self) -> FeedConfigSummary:
        raise NotImplementedError(f"{type(self).__name__} must implement summary()")

    def merge_preserving(self, prior: "FeedConfig") -> "FeedConfig":
        """Edit round-trip: return self with state that must NOT reset on
        an edit carried over from `prior` (per-stream poll watermarks).
        No safe default: a passthrough would cold-start every stream."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement merge_preserving() (no safe default: would reset watermarks)"
        )


class CuratedFeedConfig(FeedConfig):
    """Schema for Feed.data when Feed.kind == 'curated'.

    Streams are user-maintained (vs the future 'discovered' kind, whose
    streams are produced by a walk). Watermarks live per-stream on each
    StreamWatch; the "no future watermark" + retention-bound guards are
    server policy (see core `feeds.policy`)."""

    FEED_KIND: ClassVar[str] = "curated"

    streams: list[StreamWatch] = []
    # Item-log retention window. Bounds checked in policy ([1, 365]).
    retention_days: int = 30

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """Curated feeds carry no secrets (stream specs are public
        identities), so this is a plain dump. The contract is here so a
        future secret-bearing kind can't ship without implementing it."""
        return self.model_dump(mode="json")

    def summary(self) -> FeedConfigSummary:
        displays = [w.spec.display() for w in self.streams]
        return FeedConfigSummary(streams=displays, stream_count=len(displays))

    def merge_preserving(self, prior: "FeedConfig") -> "CuratedFeedConfig":
        """Carry forward per-stream `last_event_at`, keyed by spec
        identity, so editing the stream set (or retention) doesn't
        cold-start an unchanged stream. New streams (no prior match) keep
        their submitted value (None = cold-start). retention_days is not
        preserved: the submitted value wins (operator-editable)."""
        prior_streams = getattr(prior, "streams", [])
        watermarks = {w.spec.model_dump_json(): w.last_event_at for w in prior_streams}
        streams = [
            w.model_copy(update={"last_event_at": watermarks[key]})
            if (key := w.spec.model_dump_json()) in watermarks
            else w
            for w in self.streams
        ]
        return self.model_copy(update={"streams": streams})


# ── Wire (read-path response envelope) ────────────────────────────────────


class FeedItemWire(BaseModel):
    """One persisted FeedItem on the wire — the "sort by new and go" unit.

    `data` is the connector Observation's dump (opaque to the CLI; the
    server owns the per-source schema). Datetimes stay real; the renderer
    ISO-encodes them."""

    id: str
    source: str
    # Display label of the producing sub-source (e.g. "r/ClaudeCowork"),
    # set on the FeedItem row at record time from the StreamSpec's
    # `.display()`. Read directly off the column — no parsing.
    stream: str = ""
    external_id: str
    occurred_at: Any = None  # datetime | None; renderer encodes
    data: ConfigBlob = {}


class FeedWire(BaseModel):
    """The kind-independent envelope every `/v1/feeds` response item
    carries. List-item shape and base for detail / mutation responses.
    Mirrors `ListenerWire`."""

    id: str
    name: str
    kind: str
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: Any = None  # datetime | None
    next_poll_at: Any = None
    # creator, audit/display only (account-scoped reads, not an ownership filter)
    user_id: str
    data: ConfigBlob = {}
    created_at: Any = None


class FeedListResponse(BaseModel):
    """`GET /v1/feeds` -> `{"items": [...], "next_cursor": <id>|None}`.

    Cursor-paginated by ULID pk, newest-first. Pass `?after=<id>` to fetch
    the next page; `next_cursor` is the id to send back, or null when the
    page wasn't full (= no more rows)."""

    items: list[FeedWire] = []
    next_cursor: str | None = None


class FeedView(FeedWire):
    """`GET /v1/feeds/<id>` - read view: envelope + display `summary` +
    the recent item log (this is the "sort by new and go" surface; the
    detail endpoint IS the reader, no separate route needed)."""

    summary: FeedConfigSummary = FeedConfigSummary()
    recent_items: list[FeedItemWire] = []


class FeedMutationResponse(FeedWire):
    """Create / edit response (POST + PUT, real and `?dry_run=true`).
    `id` absent on a create dry-run preview; `dry_run` True for preview."""

    id: str | None = None
    summary: FeedConfigSummary = FeedConfigSummary()
    dry_run: bool
