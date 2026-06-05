"""`/v1/deliveries/<delivery_id>` — one delivery's detail (with the sent
request_payload), addressed by its own globally-unique ULID.

The LIST lives under the action (`/v1/actions/<id>/deliveries`, lean rows) ;
the detail is by leaf id, mirroring `/v1/actions/<id>`. Mounted at
`/v1/deliveries` in `conf.urls`.
"""

from common.urls import api_path

from . import views_audit

urlpatterns = [
    api_path(
        "<str:delivery_id>",
        views_audit.ActionDeliveryDetailView.as_view(),
        name="delivery_detail",
    ),
]
