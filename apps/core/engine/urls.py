from common.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.EngineListView.as_view(), name="engine_list"),
]
