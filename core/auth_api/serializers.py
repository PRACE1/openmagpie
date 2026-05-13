"""DRF serializers for the auth API.

Input serializers validate request payloads (DRF turns ValidationError
into 400). Output serializers shape responses, both the web frontend
(`web/packages/api-utils/src/types.ts`) and the CLI
(`cli/src/openmagpie/api/auth.py`) read these shapes verbatim, so
changes here ripple to both clients.
"""

from __future__ import annotations

from accounts.services import AccountService
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .constants import BEARER_TOKEN_TYPE


# ── Input ──────────────────────────────────────────────────────────────


class _EmailField(serializers.EmailField):
    """Lowercased, stripped email. Matches the model's normalize step."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().lower()


class SignupSerializer(serializers.Serializer):
    email = _EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value: str) -> str:
        # Runs the project's AUTH_PASSWORD_VALIDATORS chain (min length, etc.).
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = _EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class RefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


# ── Output ─────────────────────────────────────────────────────────────


class UserSerializer(serializers.Serializer):
    """Wire shape for the current user (`/v1/auth/me`, signup / login
    `{user}` responses). Fed an `accounts.User` instance.
    """

    id = serializers.CharField()
    email = serializers.EmailField()
    account_id = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="date_joined", allow_null=True)

    def get_account_id(self, user) -> str | None:
        return AccountService.Global.primary_account_id_for(user_id=str(user.id))


class TokenPairSerializer(serializers.Serializer):
    """Wire shape for `/v1/auth/tokens/refresh` responses and the
    device-session completed bag. Fed a dict assembled in the view
    (OAuth Toolkit's models don't natively carry `expires_in` etc.).

    Use `TokenPairSerializer.build(user, access, refresh, ttl).data`
    when you have the raw token rows handy.
    """

    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    token_type = serializers.CharField()
    user = UserSerializer()

    @classmethod
    def build(cls, user, access, refresh, ttl: int) -> TokenPairSerializer:
        return cls(
            instance={
                "access_token": access.token,
                "refresh_token": refresh.token,
                "expires_in": ttl,
                "token_type": BEARER_TOKEN_TYPE,
                "user": user,
            }
        )
