"""Listeners API resource client.

Wraps the `/v1/listeners` endpoints. The request body's kind-specific
`data` payload is validated server-side via the Pydantic registry, so
the CLI doesn't mirror every schema (no type bump per new kind).

Response models are NOT hand-defined here. They live ONCE in the shared
`openmagpie-schema` package (`openmagpie_schema.wire`), populated by the
server, imported verbatim by this client - one definition, no
cross-boundary copy. Only `ListenerEnvelope` (the request envelope the
CLI *constructs*: template + create options + edit seed) is CLI-owned.
"""

from __future__ import annotations

import builtins
from typing import Any

from pydantic import BaseModel

from openmagpie_schema.wire import (
    ConfigBlob,
    ListenerListResponse,
    ListenerMutationResponse,
    ListenerView,
    ListenerWire,
)

from .. import routes
from ..http import MagpieClient

__all__ = [
    "ConfigBlob",
    "ListenerApi",
    "ListenerEnvelope",
    "ListenerListResponse",
    "ListenerMutationResponse",
    "ListenerView",
    "ListenerWire",
]


class ListenerEnvelope(BaseModel):
    """The kind-INDEPENDENT envelope the CLI *constructs* for a write
    (request side), regardless of `kind`. CLI-owned (the template +
    create options + the edit seed all encode exactly this), distinct
    from the server-emitted response models in `openmagpie_schema.wire`.

    `kind` is the top-level discriminator the server lanes on to pick the
    `data` validator. Extra keys are ignored (server ignores them too);
    `data` is passed through untouched for the server to validate.
    """

    name: str
    instructions: str
    kind: str
    delivery_mode: str
    poll_interval_seconds: int
    data: ConfigBlob = {}

    model_config = {"extra": "ignore"}


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

    def list(self) -> builtins.list[ListenerWire]:
        # builtins.list: the method is named `list`, which shadows the
        # builtin inside its own (deferred) annotation scope.
        """List listeners in the caller's account, newest-first.

        Server returns `{"items": [<listener>, ...]}`; parsed through
        `ListenerListResponse` so the envelope and each item go through
        the same typed path (no ad-hoc dict poking).
        """
        raw = self._http.get(routes.listeners.collection)
        return ListenerListResponse.model_validate(raw).items

    def get(self, listener_id: str) -> ListenerView:
        """GET one listener (account-scoped). 404 -> ApiError(status=404)."""
        raw = self._http.get(routes.listeners.detail(listener_id))
        return ListenerView.model_validate(raw)

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
