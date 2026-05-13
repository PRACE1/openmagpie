from common.views import healthz
from django.conf import settings
from django.urls import include, path

# NOTE: we intentionally do NOT mount `oauth2_provider.urls`. The Toolkit
# models (AccessToken / RefreshToken / Application) are useful storage
# primitives that our own /v1/auth/* views compose with directly; we have
# no need for Toolkit's HTTP surface (which would expose /oauth/token's
# password / client_credentials grants and bypass our login/audit
# pipeline). Reinstate only if you build a real OAuth-provider flow.
urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path(f"{settings.API_VERSION_PREFIX}/auth/", include("auth_api.urls")),
]
