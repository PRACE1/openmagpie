from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "",
        views.ListenerListCreateView.as_view(),
        name="listener_list_create",
    ),
    api_path(
        "<str:listener_id>",
        views.ListenerDetailView.as_view(),
        name="listener_detail",
    ),
]
