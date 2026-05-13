from django.urls import path

from . import device_sessions, views

urlpatterns = [
    # Browser session lifecycle.
    path("signup", views.SignupView.as_view(), name="auth_signup"),
    path("login", views.LoginView.as_view(), name="auth_login"),
    path("logout", views.LogoutView.as_view(), name="auth_logout"),
    path("me", views.MeView.as_view(), name="auth_me"),
    # CLI bearer-token lifecycle.
    path(
        "tokens/refresh",
        views.TokensRefreshView.as_view(),
        name="auth_tokens_refresh",
    ),
    path(
        "tokens/revoke",
        views.TokensRevokeView.as_view(),
        name="auth_tokens_revoke",
    ),
    # CLI ↔ browser handshake.
    path(
        "device-sessions",
        device_sessions.DeviceSessionsCreateView.as_view(),
        name="auth_device_sessions_create",
    ),
    path(
        "device-sessions/<str:session_id>",
        device_sessions.DeviceSessionPollView.as_view(),
        name="auth_device_session_poll",
    ),
    path(
        "device-sessions/<str:session_id>/info",
        device_sessions.DeviceSessionInfoView.as_view(),
        name="auth_device_session_info",
    ),
    path(
        "device-sessions/<str:session_id>/complete",
        device_sessions.DeviceSessionCompleteView.as_view(),
        name="auth_device_session_complete",
    ),
    path(
        "device-sessions/<str:session_id>/deny",
        device_sessions.DeviceSessionDenyView.as_view(),
        name="auth_device_session_deny",
    ),
    # Diagnostics.
    path("whoami", views.WhoamiView.as_view(), name="auth_whoami"),
]
