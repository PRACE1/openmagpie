"""Listeners API resource client.

Wraps `POST /v1/listeners` and `GET /v1/listeners`. The body shape is
intentionally typed as plain dict on the wire: the kind-specific
`data` payload is validated server-side via the Pydantic registry, so
the CLI doesn't need to mirror every schema (and doesn't have to bump
its types every time a new Listener kind ships).

Response models are slim, just the fields the CLI's `list` / `create`
output actually renders. The full response carries more (last_polled_at,
next_poll_at, etc.); we leave it as a passthrough dict on `raw` for
callers that want it.
"""

from __future__ import annotations

import builtins
from typing import Any

from pydantic import BaseModel

from .. import routes
from ..http import MagpieClient


class ListenerSummary(BaseModel):
    """Slim view of a Listener for `magpie listener list` output."""

    id: str
    name: str
    kind: str
    delivery_mode: str
    is_active: bool


class ListenerListResponse(BaseModel):
    """Envelope for `GET /v1/listeners` (`{"items": [...]}`).

    Modeled so the client parses the envelope the same typed way it
    parses each item, instead of ad-hoc `raw.get("items", [])` poking.
    Unknown top-level keys are ignored by Pydantic default.
    """

    items: list[ListenerSummary] = []


class ListenerConfigSummary(BaseModel):
    """Server-built display projection of the config (see the server's
    `ListenerConfigSummary`). The CLI prints these strings as-is and
    never parses the opaque `data` blob - no schema knowledge here."""

    streams: list[str] = []
    notifiers: list[str] = []
    engine: str = ""


class ListenerMutationResponse(BaseModel):
    """Typed envelope for create / dry-run (`POST /v1/listeners`).

    Only the fields the CLI actually consumes are modeled; everything
    else the server sends (the raw `data` blob, last_polled_at, ...) is
    ignored by Pydantic default. The CLI never parses the config blob:
    the server emits a typed `summary` projection (schema knowledge
    lives only on the server), so there's nothing here to mirror and
    nothing to drift.

    `id` is absent on the dry-run preview (the server strips the pre-save
    placeholder), hence optional. `dry_run` is True for a preview, False
    for a real create.
    """

    id: str | None = None
    name: str
    kind: str
    delivery_mode: str
    instructions: str
    poll_interval_seconds: int
    dry_run: bool
    # Display projection built server-side from the typed config; the
    # CLI renders it as-is.
    summary: ListenerConfigSummary = ListenerConfigSummary()


class ListenerDetail(BaseModel):
    """`GET /v1/listeners/<id>` - the read view, and the round-trip
    payload for edit.

    `data` is the server's REDACTED config blob. It is opaque to the
    CLI: never parsed (no shadow schema), only dumped back into $EDITOR
    for `edit`, where the server re-validates and restores `***` secrets
    + watermarks. Display uses the typed `summary`, never `data`.
    """

    id: str
    name: str
    kind: str
    delivery_mode: str
    instructions: str
    poll_interval_seconds: int
    is_active: bool
    created_at: str | None = None
    last_polled_at: str | None = None
    next_poll_at: str | None = None
    summary: ListenerConfigSummary = ListenerConfigSummary()
    data: dict[str, Any] = {}


class ListenerApi:
    """Resource client for `/v1/listeners`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any], *, dry_run: bool = False) -> ListenerMutationResponse:
        """POST a listener. Returns the parsed `ListenerMutationResponse`.
        Server-side validation errors propagate as `ApiError` (status=400)
        carrying the per-field detail in `e.body`.

        `dry_run=True` adds `?dry_run=true`: the server runs the identical
        validation and returns the validated record (with `dry_run: true`)
        WITHOUT persisting. Used for the preview/confirm step. It is a
        validation preview, not a create-success guarantee.
        """
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.listeners.collection, json_body=body, params=params)
        return ListenerMutationResponse.model_validate(raw)

    def list(self) -> builtins.list[ListenerSummary]:
        # builtins.list: the method is named `list`, which shadows the
        # builtin inside its own (deferred) annotation scope.
        """List listeners in the caller's account, newest-first.

        Server returns `{"items": [<listener>, ...]}`; parsed through
        `ListenerListResponse` so the envelope and each item go through
        the same typed path (no ad-hoc dict poking).
        """
        raw = self._http.get(routes.listeners.collection)
        return ListenerListResponse.model_validate(raw).items

    def get(self, listener_id: str) -> ListenerDetail:
        """GET one listener (account-scoped). 404 -> ApiError(status=404)."""
        raw = self._http.get(routes.listeners.detail(listener_id))
        return ListenerDetail.model_validate(raw)

    def update(self, listener_id: str, body: dict[str, Any], *, dry_run: bool = False) -> ListenerMutationResponse:
        """PUT a full-replace edit. Same contract as `create` (envelope
        validation, `?dry_run=true` preview, same response shape). The
        server keeps `kind` immutable, and preserves watermarks + `***`
        secrets the operator left masked."""
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.listeners.detail(listener_id), json_body=body, params=params)
        return ListenerMutationResponse.model_validate(raw)

    def delete(self, listener_id: str) -> None:
        """DELETE one listener. 204 on success; 404 -> ApiError."""
        self._http.delete(routes.listeners.detail(listener_id))
