from common.urls import api_path

from . import views

urlpatterns = [
    api_path(
        "",
        views.ListenerListCreateView.as_view(),
        name="listener_list_create",
    ),
]
