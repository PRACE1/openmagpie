"""DRF serializers for the listeners API.

`ListenerCreateSerializer` validates the envelope and delegates the
kind-specific `data` blob to the Pydantic registry. Pydantic errors are
re-shaped into DRF's nested 400 format so a `data.streams[0].spec.kind`
problem surfaces at the right path on the client.

`ListenerSerializer` is the flat output shape, fed an ORM `Listener`.
"""

from __future__ import annotations

from typing import Any

from listeners.models import Listener
from listeners.registry import get_config_class
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from .models.listener import MIN_POLL_INTERVAL_SECONDS


# ── Input ──────────────────────────────────────────────────────────────


class ListenerCreateSerializer(serializers.Serializer):
    """Envelope for POST /v1/listeners.

    The kind-specific config blob arrives as `data`; we validate it via
    the Pydantic registry so each Listener kind owns its own schema.
    """

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    instructions = serializers.CharField(trim_whitespace=True)
    kind = serializers.CharField(max_length=32)
    delivery_mode = serializers.ChoiceField(
        choices=[m.value for m in Listener.DeliveryMode],
        default=Listener.DeliveryMode.INSTANT.value,
    )
    poll_interval_seconds = serializers.IntegerField(
        min_value=MIN_POLL_INTERVAL_SECONDS,
        default=300,
    )
    data = serializers.DictField(child=serializers.JSONField())

    def validate_kind(self, value: str) -> str:
        try:
            get_config_class(value)
        except KeyError:
            raise serializers.ValidationError(f"unknown listener kind {value!r}")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # `data` is validated against the kind-specific Pydantic class.
        # We do it in `validate()` (not `validate_data()`) because we
        # need the already-validated `kind` field to pick the schema.
        config_class = get_config_class(attrs["kind"])
        try:
            validated = config_class.model_validate(attrs["data"])
        except PydanticValidationError as exc:
            raise serializers.ValidationError(
                {"data": _pydantic_errors_to_drf(exc)}
            ) from exc
        # Replace the raw dict with the normalized Pydantic dump so the
        # service layer stores a canonical shape regardless of input
        # ordering or omitted defaults.
        attrs["data"] = validated.model_dump(mode="json")
        return attrs


def _pydantic_errors_to_drf(exc: PydanticValidationError) -> dict[str, Any]:
    """Re-shape Pydantic's flat error list into DRF's nested error dict.

    Pydantic's `loc` is a tuple of path segments (`('streams', 0,
    'spec', 'kind')`); DRF wants nested dicts mirroring the request
    shape so the frontend can map errors to form fields by path.
    """
    out: dict[str, Any] = {}
    for err in exc.errors():
        cursor: Any = out
        path = list(err["loc"])
        for segment in path[:-1]:
            key = str(segment)
            if key not in cursor or not isinstance(cursor[key], dict):
                cursor[key] = {}
            cursor = cursor[key]
        leaf_key = str(path[-1]) if path else "__root__"
        cursor.setdefault(leaf_key, []).append(err["msg"])
    return out


# ── Output ─────────────────────────────────────────────────────────────


class ListenerSerializer(serializers.Serializer):
    """Wire shape for a Listener record. Fed an ORM `Listener` instance."""

    id = serializers.CharField()
    name = serializers.CharField()
    instructions = serializers.CharField()
    kind = serializers.CharField()
    delivery_mode = serializers.CharField()
    is_active = serializers.BooleanField()
    poll_interval_seconds = serializers.IntegerField()
    last_polled_at = serializers.DateTimeField(allow_null=True)
    next_poll_at = serializers.DateTimeField(allow_null=True)
    last_digest_at = serializers.DateTimeField(allow_null=True)
    next_digest_at = serializers.DateTimeField(allow_null=True)
    data = serializers.JSONField()
    created_at = serializers.DateTimeField()
