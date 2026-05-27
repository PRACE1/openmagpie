from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "",
        views.FeedListCreateView.as_view(),
        name="feed_list_create",
    ),
    api_path(
        "<str:feed_id>",
        views.FeedDetailView.as_view(),
        name="feed_detail",
    ),
]
