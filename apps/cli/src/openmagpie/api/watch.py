"""Watches API resource client.

Wraps the `/v1/watches` endpoints. ALL shapes live ONCE in the shared
`openmagpie_schema.watch` package (the server is the authority) and are
imported verbatim here — including the write envelope `WatchInput`, which
the CLI constructs. No CLI-side copy. Mirrors `api/feed.py`.
"""

from __future__ import annotations

import builtins
from typing import Any

from openmagpie_schema.watch import (
    WatchActionDeliveryListResponse,
    WatchActionDeliveryView,
    WatchActionRunListResponse,
    WatchActionWire,
    WatchInput,
    WatchListResponse,
    WatchMutationResponse,
    WatchView,
    WatchWire,
)

from .. import routes
from ..http import MagpieClient

__all__ = [
    "WatchApi",
    "WatchInput",
    "WatchListResponse",
    "WatchMutationResponse",
    "WatchView",
    "WatchWire",
]


def _list_params(
    *, state: str | None = None, after: str | None = None, limit: int | None = None, window: str | None = None
) -> dict[str, str] | None:
    """The cursor-list query params shared by the action runs / deliveries
    endpoints, dropping the unset ones. None when empty so httpx sends none."""
    params: dict[str, str] = {}
    if state:
        params["state"] = state
    if after:
        params["after"] = after
    if limit is not None:
        params["limit"] = str(limit)
    if window:
        params["window"] = window
    return params or None


class WatchApi:
    """Resource client for `/v1/watches`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any], *, dry_run: bool = False) -> WatchMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.watches.collection, json_body=body, params=params)
        return WatchMutationResponse.model_validate(raw)

    def list(self, *, after: str | None = None, limit: int | None = None) -> WatchListResponse:
        """One page of watches (cursor-paginated, newest-first by ULID pk).
        `after` = id of the last watch from the previous page; omit on the
        first call. `next_cursor` is None when there are no more rows."""
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        raw = self._http.get(routes.watches.collection, params=params or None)
        return WatchListResponse.model_validate(raw)

    def get(self, watch_id: str) -> WatchView:
        """GET one watch (account-scoped). Carries the initial path's
        ordered action chain."""
        raw = self._http.get(routes.watches.detail(watch_id))
        return WatchView.model_validate(raw)

    def update(self, watch_id: str, body: dict[str, Any], *, dry_run: bool = False) -> WatchMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.watches.detail(watch_id), json_body=body, params=params)
        return WatchMutationResponse.model_validate(raw)

    def delete(self, watch_id: str) -> None:
        self._http.delete(routes.watches.detail(watch_id))

    # ── Actions sub-resource (the chain) ────────────────────────────────

    def list_actions(self, watch_id: str) -> builtins.list[WatchActionWire]:
        raw = self._http.get(routes.watches.actions(watch_id))
        items = (raw or {}).get("items") or []
        return [WatchActionWire.model_validate(it) for it in items]

    def add_action(
        self, watch_id: str, kind: str, config: dict[str, Any], *, rank: int | None = None
    ) -> WatchActionWire:
        body: dict[str, Any] = {"kind": kind, "config": config}
        if rank is not None:
            body["rank"] = rank
        raw = self._http.post(routes.watches.actions(watch_id), json_body=body)
        return WatchActionWire.model_validate(raw)

    def set_action(self, action_id: str, kind: str, config: dict[str, Any]) -> WatchActionWire:
        raw = self._http.put(routes.actions.detail(action_id), json_body={"kind": kind, "config": config})
        return WatchActionWire.model_validate(raw)

    def remove_action(self, action_id: str) -> None:
        self._http.delete(routes.actions.detail(action_id))

    def action_runs(
        self,
        action_id: str,
        *,
        state: str | None = None,
        after: str | None = None,
        limit: int | None = None,
        window: str | None = None,
    ) -> WatchActionRunListResponse:
        # window is the activity-summary preset (server resolves it to bounds).
        params = _list_params(state=state, after=after, limit=limit, window=window)
        raw = self._http.get(routes.actions.runs(action_id), params=params)
        return WatchActionRunListResponse.model_validate(raw)

    def action_deliveries(
        self,
        action_id: str,
        *,
        state: str | None = None,
        after: str | None = None,
        limit: int | None = None,
    ) -> WatchActionDeliveryListResponse:
        params = _list_params(state=state, after=after, limit=limit)
        raw = self._http.get(routes.actions.deliveries(action_id), params=params)
        return WatchActionDeliveryListResponse.model_validate(raw)

    def action_delivery(self, delivery_id: str) -> WatchActionDeliveryView:
        raw = self._http.get(routes.deliveries.detail(delivery_id))
        return WatchActionDeliveryView.model_validate(raw)
