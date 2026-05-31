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
    api_path(
        "<str:listener_id>/rewind",
        views.ListenerRewindView.as_view(),
        name="listener_rewind",
    ),
    api_path(
        "<str:listener_id>/payload-sample",
        views.ListenerPayloadSampleView.as_view(),
        name="listener_payload_sample",
    ),
    api_path(
        "<str:listener_id>/hits",
        views.ListenerHitsView.as_view(),
        name="listener_hits",
    ),
]
