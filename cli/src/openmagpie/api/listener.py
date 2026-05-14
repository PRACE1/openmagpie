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


class ListenerApi:
    """Resource client for `/v1/listeners`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST a listener. Returns the full server response dict so the
        caller can echo `id`, `name`, or anything else. Server-side
        validation errors propagate as `ApiError` (status=400) carrying
        the per-field detail in `e.body`."""
        return self._http.post(routes.listeners.collection, json_body=body)

    def list(self) -> list[ListenerSummary]:
        """List listeners in the caller's account, newest-first.

        Server returns `{"items": [<listener>, ...]}`; we project each
        item to a `ListenerSummary` so command code doesn't paw through
        keys it doesn't render.
        """
        raw = self._http.get(routes.listeners.collection)
        items = raw.get("items", []) if isinstance(raw, dict) else []
        return [ListenerSummary.model_validate(item) for item in items]
