"""Listener API wire shapes (the server-emitted response envelope).

SHARED, zero-Django source of truth, the read-path counterpart to
`configs` (the write-path `data` blob). The server builds every
`/v1/listeners` response THROUGH these models (`serializers.listener_*`),
so they are the authority, not a CLI guess: the magpie CLI (and any
future MCP / API consumer) imports the *same* classes instead of
hand-mirroring the response. One definition, no cross-boundary copy,
no drift.

`data` stays opaque here (`ConfigBlob`): the server validates it via
the Pydantic registry keyed by `kind`; modelling its interior would
re-mirror that schema and drift the moment a kind ships. Display uses
the typed `summary` projection (`ListenerConfigSummary`), never `data`.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .configs import ListenerConfigSummary

ConfigBlob = dict[str, Any]
"""A listener's kind-specific `data` config, opaque on the wire.

The server (Pydantic registry, keyed by `kind`) is the sole validator.
Carried verbatim by readers - YAML round-trip for edit, display via the
typed `summary` - never field-read. The envelope around it IS typed."""


class ListenerWire(BaseModel):
    """The kind-independent envelope every `/v1/listeners` response item
    carries (built by `serializers.listener_wire`).

    This is the list-item shape and the base for the detail / mutation
    responses. Datetimes are real `datetime` (None pre-save for an
    unsaved dry-run instance); JSON encoding is the renderer's job.
    """

    id: str
    name: str
    instructions: str
    kind: str
    delivery_mode: str
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None = None
    next_poll_at: datetime | None = None
    last_digest_at: datetime | None = None
    next_digest_at: datetime | None = None
    # creator, audit/display only - account-scoped reads mean this is
    # NOT an ownership filter (see core ListenerService).
    user_id: str
    data: ConfigBlob = {}
    created_at: datetime | None = None


class ListenerListResponse(BaseModel):
    """`GET /v1/listeners` -> `{"items": [...]}`.

    Envelope and items go through the same typed path (no ad-hoc
    `raw["items"]` poking). Unknown top-level keys ignored by default."""

    items: list[ListenerWire] = []


class ListenerView(ListenerWire):
    """`GET /v1/listeners/<id>` - the read view. The envelope plus the
    server-built display `summary` (the CLI renders it as-is and never
    parses the opaque `data` blob - schema knowledge stays server-side).
    Also the round-trip source for `edit` (operator edits the text;
    server re-validates and restores `***` secrets + watermarks)."""

    summary: ListenerConfigSummary = ListenerConfigSummary()


class ListenerMutationResponse(ListenerWire):
    """Create / edit response (POST + PUT, real and `?dry_run=true`).

    `id` is absent on the create dry-run preview (the server omits the
    pre-save placeholder), hence `str | None`. `dry_run` is True for a
    validation-only preview, False for a persisted write. `summary` is
    the server-built display projection."""

    id: str | None = None
    summary: ListenerConfigSummary = ListenerConfigSummary()
    dry_run: bool
