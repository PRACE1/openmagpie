"""Watch API wire shapes (the server-emitted response envelopes) + the
write-side input envelopes the CLI constructs.

SHARED, zero-Django source of truth. The server builds every `/v1/watches`
response THROUGH these (server is the authority) ; the magpie CLI imports
the SAME classes and validates responses against them, so there's no
hand-mirrored copy to drift. The per-kind action `config` / `result` blobs
stay opaque here (`ConfigBlob` / `ResultBlob`) ; their strict shapes live
in `watch_actions.py`, validated server-side by a kind-keyed registry.

Mirrors `feed.py` (envelope quartet: Wire / ListResponse / View /
MutationResponse) deliberately, so the two primitives read the same.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .watch_actions import WatchActionConfigSummary
from .watch_enums import WatchActionRunState
from .wire import ConfigBlob

ResultBlob = dict[str, Any]
"""A WatchActionRun's kind-specific `result`, opaque on the wire.

The runner writes a kind-strict result (see `watch_actions`); readers
carry it verbatim and render common keys best-effort."""


# ── Action chain (nested under a watch's path) ────────────────────────────


class WatchActionWire(BaseModel):
    """One action node on the wire. `config` is the kind-specific blob
    (opaque here; the server validated it via the registry on write).
    `summary` is the server-built display projection so the CLI never
    parses `config`."""

    id: str
    kind: str
    rank: int
    config: ConfigBlob = Field(default_factory=dict)
    summary: WatchActionConfigSummary = WatchActionConfigSummary()
    created_at: datetime | None = None


class WatchActionInput(BaseModel):
    """One action on a create / edit / add-action request: `{id?, kind,
    config}` with `kind` adjacent to its blob (k8s-style). `kind` selects
    the action type ; the server validates `config` against it via the
    registry, so the persisted blob is the pure kind-specific shape (no
    `kind` nested inside). `rank` is optional on input (append when
    omitted); the server owns the dense renumber. Extra keys ignored so an
    edit seed's read-only fields drop on round-trip.

    `id` is the STABLE identity of an existing action, carried back on a
    whole-chain edit so the server matches by id (NOT list position):
    matched actions are updated in place ; their id + run history survive,
    and a masked secret restores from that same row. Omit `id` (or leave it
    empty) for a brand-new action ; the server mints its id. A non-empty id
    that isn't on the watch is rejected."""

    id: str = ""
    kind: str
    config: ConfigBlob = Field(default_factory=dict)
    rank: int | None = None

    model_config = {"extra": "ignore"}


# ── Watch envelope (read path) ────────────────────────────────────────────


class WatchWire(BaseModel):
    """The envelope every `/v1/watches` response item carries. List-item
    shape and base for the detail / mutation responses.

    `feed_ids` is the watch's subscription set (the WatchFeed rows,
    minus the internal per-feed watermark which never crosses the wire).
    Datetimes are real `datetime` (None pre-save); JSON encoding is the
    renderer's job. `user_id` is creator/audit only (account-scoped
    reads, not an ownership filter)."""

    id: str
    name: str
    is_active: bool
    feed_ids: list[str] = Field(default_factory=list)
    user_id: str
    created_at: datetime | None = None


class WatchListResponse(BaseModel):
    """`GET /v1/watches` -> `{"items": [...], "next_cursor": <id>|None}`.

    Cursor-paginated by ULID pk, newest-first. Pass `?after=<id>` for the
    next page; `next_cursor` is the id to send back, or null when the
    page wasn't full (no more rows)."""

    items: list[WatchWire] = Field(default_factory=list)
    next_cursor: str | None = None


class WatchView(WatchWire):
    """`GET /v1/watches/<id>`, the read view: the envelope plus the
    ordered action chain of the watch's initial path. v1 has exactly one
    path, so `actions` is that path's actions by `rank` ; the path layer
    stays hidden on the wire until multi-path ships."""

    actions: list[WatchActionWire] = Field(default_factory=list)


class WatchMutationResponse(WatchWire):
    """Create / edit response (POST + PUT, real and `?dry_run=true`).
    `id` is absent on a create dry-run preview (server omits the pre-save
    placeholder), hence `str | None`. `dry_run` is True for a
    validation-only preview. Carries the same `actions` enrichment as
    WatchView so the CLI's confirm-preview shows the resulting chain."""

    id: str | None = None
    actions: list[WatchActionWire] = Field(default_factory=list)
    dry_run: bool


class WatchInput(BaseModel):
    """The envelope the CLI constructs for a watch write (request side).
    CLI-owned, distinct from the server-emitted models. `feed_ids` is the
    subscription set; `actions` is the initial path's ordered chain. The
    server creates the Watch + its single WatchPath + WatchFeed rows
    atomically. Extra keys ignored so an edit seed's read-only fields
    drop on round-trip."""

    name: str
    is_active: bool = True
    feed_ids: list[str] = Field(default_factory=list)
    actions: list[WatchActionInput] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


# ── ActionRun (audit log read path) ───────────────────────────────────────


class WatchActionRunWire(BaseModel):
    """One WatchActionRun on the wire (`GET /v1/watches/<id>/actions/<id>/runs`).

    The stateful audit row of one action executing against one item.
    `result` is the kind-specific output blob (opaque; render common keys
    best-effort). `state` is the `WatchActionRunState` value. Datetimes
    real; renderer encodes."""

    id: str
    watch_id: str
    action_id: str
    feed_item_id: str
    state: WatchActionRunState
    result: ResultBlob = Field(default_factory=dict)
    error: str = ""
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class WatchActionRunListResponse(BaseModel):
    """`GET /v1/watches/<id>/actions/<action_id>/runs` envelope.
    Cursor-paginated by ULID pk, newest-first. `?after=<id>` for the next
    page; `next_cursor` null when the page wasn't full. Filter by
    `?state=` (a WatchActionRunState value)."""

    items: list[WatchActionRunWire] = Field(default_factory=list)
    next_cursor: str | None = None
