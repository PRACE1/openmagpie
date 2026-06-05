"""`/v1/actions/<action_id>` — per-action ops addressed by the action's own
(globally unique) ULID, not nested under its watch.

A WatchAction id uniquely identifies the action AND its account/watch, so
the leaf id is all the route needs ; the watch id would be redundant. The
chain-level list/add stay watch-scoped in `watches.urls` (no action id
exists yet there). Mounted at `/v1/actions` in `conf.urls`.
"""

from common.urls import api_path

from . import views, views_audit

urlpatterns = [
    api_path(
        "<str:action_id>",
        views.ActionDetailView.as_view(),
        name="action_detail",
    ),
    api_path(
        "<str:action_id>/runs",
        views_audit.ActionRunsView.as_view(),
        name="action_runs",
    ),
    api_path(
        "<str:action_id>/deliveries",
        views_audit.ActionDeliveriesView.as_view(),
        name="action_deliveries",
    ),
]
