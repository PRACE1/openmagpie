"""Listeners API wire shapes.

Input is a DRF serializer (`ListenerCreateSerializer`) because that's
the HTTP request-validation boundary: field rules + the flat-path 400
errors, delegating the `data` blob to the Pydantic registry.

Output is NOT a DRF serializer. The response shapes live ONCE in the
shared `openmagpie-schema` package (`wire`); the builders here populate
those models so the server is their authority and the CLI imports the
same classes (no hand-mirrored field list, no `data`-shadows-`.data`
cast). `listener_wire` / `listener_view` / `listener_mutation` are the
single source used by list / create / dry-run / get / edit.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from common.pydantic_errors import pydantic_errors_to_drf
from listeners.models import Listener
from listeners.policy import PolicyError
from listeners.registry import get_config_class, load_config, validate_config
from listeners.stats import DEFAULT_WINDOW_DAYS, compute_hit_rates
from openmagpie_schema.configs import ListenerConfigSummary
from openmagpie_schema.wire import (
    ListenerMutationResponse,
    ListenerView,
    ListenerWire,
)

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
    data = serializers.DictField(child=serializers.JSONField())

    def validate_kind(self, value: str) -> str:
        try:
            get_config_class(value)
        except KeyError:
            raise serializers.ValidationError(f"unknown listener kind {value!r}") from None
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # `data` is validated against the kind-specific Pydantic class.
        # We do it in `validate()` (not `validate_data()`) because we
        # need the already-validated `kind` field to pick the schema.
        # validate_config = model_validate (shape) + enforce_policy
        # (engine-kind registered + default-fill, no future watermark,
        # webhook SSRF). One call, one source - the callsite can't run
        # shape validation and forget the policy half. Each failure mode
        # still maps to its own 400: shape -> per-field paths, policy ->
        # {"data": [msg]}.
        try:
            validated = validate_config(attrs["kind"], attrs["data"])
        except PydanticValidationError as exc:
            raise serializers.ValidationError({"data": pydantic_errors_to_drf(exc)}) from exc
        except PolicyError as exc:
            raise serializers.ValidationError({"data": [str(exc)]}) from exc
        # Replace the raw dict with the normalized Pydantic dump so the
        # service layer stores a canonical shape regardless of input
        # ordering or omitted defaults.
        attrs["data"] = validated.model_dump(mode="json")
        return attrs


# ── Output ─────────────────────────────────────────────────────────────


def _redacted_data(listener: Listener) -> dict[str, Any]:
    """`listener.data` validated through the kind's typed config and
    redacted (the config owns what's secret - see
    configs.NotifierSpecBase.redacted).

    Per-row fail-safe: re-validation can fail for a single row (unknown
    kind, data drift, a settings-dependent validator like the webhook
    URL check). That MUST NOT 500 a `many` list - it would abort the
    whole account's list, hiding healthy listeners. On failure log and
    return a sentinel, NEVER raw `listener.data` (that would leak
    unredacted webhook secrets). The row stays visible via its model
    columns so the operator sees it exists and is broken.
    """
    try:
        config = load_config(listener)
        return config.redacted_dump()
    except Exception:
        logger.exception(
            "listener %s data failed redaction (kind=%s); returning sentinel",
            listener.id,
            listener.kind,
        )
        return {"error": "config_unreadable"}


_EMPTY_SUMMARY = ListenerConfigSummary()


def _listener_summary(listener: Listener) -> ListenerConfigSummary:
    """Display projection built from the typed config (the only schema
    owner) so the CLI never parses `data`.

    Same per-row fail-safe as `_redacted_data`: create/dry-run/edit just
    validated the config in-request, but GET-detail / list read stored
    data that could be corrupt. One bad listener must degrade to an
    empty summary, not 500 (or abort a whole `many` list). The failure
    is already logged by `_redacted_data` on the same request, so just
    default here."""
    try:
        config = load_config(listener)
        return config.summary()
    except Exception:
        return _EMPTY_SUMMARY


def listener_wire(
    listener: Listener,
    *,
    recent: tuple[int, int] = (0, 0),
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> ListenerWire:
    """The single source for a Listener's kind-independent wire envelope.
    list / create / dry-run / get / edit all build from here (via
    `listener_view` / `listener_mutation`), so the response can't drift
    between endpoints.

    `recent` is (hits, items) over the trailing `window_days` for the rolling
    hit rate; callers that have it (list/detail) pass it batched, others
    (mutation previews) leave the (0, 0) default.

    Datetimes stay real `datetime`; the renderer ISO-encodes them.
    Tolerates an unsaved instance (dry-run): `created_at` is None
    pre-save (auto_now_add), `id` is the empty-string ULID placeholder
    (the create dry-run view drops it; edit keeps the real id).
    """
    recent_hits, recent_items = recent
    return ListenerWire(
        id=str(listener.id),
        name=listener.name,
        instructions=listener.instructions,
        kind=str(listener.kind),
        delivery_mode=str(listener.delivery_mode),
        is_active=listener.is_active,
        last_judged_item_id=str(listener.last_judged_item_id or ""),
        recent_window_days=window_days,
        recent_hits=recent_hits,
        recent_items=recent_items,
        last_digest_at=listener.last_digest_at,
        next_digest_at=listener.next_digest_at,
        user_id=str(listener.user_id),
        data=_redacted_data(listener),
        created_at=listener.created_at,
    )


def listener_view(listener: Listener) -> ListenerView:
    """GET-detail response: the envelope + the server-built display
    `summary` + the rolling hit rate (computed for this one listener)."""
    recent = compute_hit_rates([listener]).get(str(listener.id), (0, 0))
    return ListenerView(
        **listener_wire(listener, recent=recent).model_dump(),
        summary=_listener_summary(listener),
    )


def listener_mutation(listener: Listener, *, dry_run: bool) -> ListenerMutationResponse:
    """Create / edit response: envelope + `summary` + `dry_run`. `id` is
    left as-is here (a create dry-run is unsaved -> empty placeholder);
    the create-dry-run view drops the key so a client never reads a
    meaningless id. Edit keeps the real id."""
    return ListenerMutationResponse(
        **listener_wire(listener).model_dump(),
        summary=_listener_summary(listener),
        dry_run=dry_run,
    )
