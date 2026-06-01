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
    api_path(
        "<str:watch_id>/actions/<str:action_id>",
        views.WatchActionDetailView.as_view(),
        name="watch_action_detail",
    ),
]
