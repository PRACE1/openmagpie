from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "",
        views.WatchListCreateView.as_view(),
        name="watch_list_create",
    ),
    api_path(
        "<str:watch_id>",
        views.WatchDetailView.as_view(),
        name="watch_detail",
    ),
    api_path(
        "<str:watch_id>/actions",
        views.WatchActionsView.as_view(),
        name="watch_actions",
    ),
    # Per-action ops (edit / remove / runs) are NOT nested here ; an action
    # ULID is globally unique, so it's addressed directly at /v1/actions/<id>
    # (see watches.action_urls). The chain-level list/add stay watch-scoped.
]
