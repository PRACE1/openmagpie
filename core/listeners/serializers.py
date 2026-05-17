"""DRF serializers for the listeners API.

`ListenerCreateSerializer` validates the envelope and delegates the
kind-specific `data` blob to the Pydantic registry. Pydantic errors are
re-shaped into DRF's nested 400 format so a `data.streams[0].spec.kind`
problem surfaces at the right path on the client.

`ListenerSerializer` is the flat output shape, fed an ORM `Listener`.
"""

from __future__ import annotations

import logging
from typing import Any

from listeners.models import Listener
from listeners.registry import get_config_class
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from .models.listener import MIN_POLL_INTERVAL_SECONDS

logger = logging.getLogger("listeners")


# ── Input ──────────────────────────────────────────────────────────────


class ListenerCreateSerializer(serializers.Serializer):
    """Envelope for POST /v1/listeners.

    The kind-specific config blob arrives as `data`; we validate it via
    the Pydantic registry so each Listener kind owns its own schema.
    """

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    # min_length floors out "" / "." / "x": instructions are fed verbatim
    # to the engine as the relevance criteria, a sub-meaningful value
    # silently burns LLM tokens producing garbage verdicts every poll.
    # The floor is deliberately low, it catches junk, not short prose.
    instructions = serializers.CharField(min_length=8, trim_whitespace=True)
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


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """Render a Pydantic `loc` tuple as a flat field path.

    `('streams', 0, 'spec', 'kind')` -> `streams[0].spec.kind`. Integer
    segments are list indices and become `[i]`; named segments are
    dot-joined. This is the exact shape `cli/AGENTS.md` documents and the
    CLI error printer expects, one key per leaf, no nested dicts (so
    sibling errors under the same parent can't collide and array-element
    paths render as `streams[0]...`, not `streams.0...`).
    """
    parts: list[str] = []
    for seg in loc:
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            parts.append(str(seg) if not parts else f".{seg}")
    return "".join(parts) or "__root__"


def _pydantic_errors_to_drf(exc: PydanticValidationError) -> dict[str, Any]:
    """Re-shape Pydantic's error list into DRF's `{path: [messages]}` dict.

    Flat, one key per leaf path (see `_loc_to_path`). Multiple messages
    for the same path accumulate in the list.
    """
    out: dict[str, list[str]] = {}
    for err in exc.errors():
        out.setdefault(_loc_to_path(tuple(err["loc"])), []).append(err["msg"])
    return out


# ── Output ─────────────────────────────────────────────────────────────


class ListenerSerializer(serializers.Serializer):
    """Wire shape for a Listener record. Fed an ORM `Listener` instance.

    Also serves the dry-run preview, which is fed an *unsaved* instance.
    `created_at` is `None` until `.save()` (auto_now_add), hence
    `allow_null`. `id` is an empty string (not null) pre-save, since
    `ULIDField` generates in `pre_save`; the dry-run view strips that
    placeholder so a client never reads a meaningless id.
    """

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
    # user_id is the creator for audit/display. Listeners are account-scoped
    # (any user in the account can read/manage them); this field shows who
    # created the listener, it is not an ownership filter. See ListenerService.
    user_id = serializers.CharField()
    data = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(allow_null=True)

    def get_data(self, obj: Listener) -> dict[str, Any]:
        """Config blob with secrets redacted, via the kind's typed config.

        Symmetric with the create path (`model_validate(...).model_dump`):
        we validate `obj.data` against the kind's config and let it
        `redacted_dump()`. What is secret is declared once on the typed
        spec (see configs.NotifierSpecBase.redacted), so the serializer
        carries no schema knowledge and can't drift when a new
        secret-bearing notifier is added.

        Per-row fail-safe: re-validation can fail for a single row
        (unknown kind, data drift, settings-dependent validators like
        the webhook URL check). That MUST NOT 500 the whole account's
        list (`many=True` would abort entirely, hiding healthy
        listeners). On failure return a redacted sentinel - never raw
        `obj.data`, which would leak unredacted webhook secrets. The row
        stays visible (its model columns: id/name/kind/...) so the
        operator sees it exists and is broken.
        """
        try:
            config = get_config_class(str(obj.kind)).model_validate(obj.data or {})
            return config.redacted_dump()
        except Exception:
            logger.exception(
                "listener %s data failed redaction (kind=%s); returning sentinel",
                obj.id,
                obj.kind,
            )
            return {"error": "config_unreadable"}
