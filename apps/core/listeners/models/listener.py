from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel

# Floor for Listener.poll_interval_seconds. Reddit's anonymous JSON endpoint
# rate-limits below ~60s/req; tighter cadences also burn LLM tokens for no real
# signal gain. Per-connector overrides can raise this further if a source needs it.
MIN_POLL_INTERVAL_SECONDS = 60


class Listener(BaseModel):
    """A single thing the user is listening for. The unit of attention in OpenMagpie.

    Common queryable fields live at the top level. Listener-kind-specific config
    (streams, engine, refined instructions, notifiers, etc.) lives inside `data`,
    validated by the Pydantic class registered for this `kind` in `listeners.registry`.
    """

    class DeliveryMode(models.TextChoices):
        INSTANT = "instant", _("Instant")
        DIGEST = "digest", _("Digest")

    user_id = models.CharField(_("user id"), max_length=26)
    account_id = models.CharField(_("account id"), max_length=26)
    kind = models.CharField(
        _("kind"),
        max_length=32,
        default="semantic",
        help_text=_("Listener kind, identifies the Pydantic config class in listeners.registry"),
    )
    name = models.CharField(_("name"), max_length=255, help_text=_("Short label"))
    instructions = models.TextField(
        _("instructions"),
        help_text=_(
            "What the engine should match against. Format depends on engine "
            "kind: natural-language prompt for semantic, comma-separated "
            "terms for a future keyword engine, etc."
        ),
    )
    is_active = models.BooleanField(_("is active"), default=True)

    poll_interval_seconds = models.PositiveIntegerField(
        _("poll interval seconds"),
        default=300,
        validators=[MinValueValidator(MIN_POLL_INTERVAL_SECONDS)],
    )
    last_polled_at = models.DateTimeField(_("last polled at"), null=True, blank=True)
    next_poll_at = models.DateTimeField(_("next poll at"), null=True, blank=True)

    # Delivery state. Allowed values: Listener.DeliveryMode.*, callers should
    # run `listeners.services.validate_delivery_mode` before save. The Django
    # field itself does not enforce choices (would force a migration for every
    # enum change).
    delivery_mode = models.CharField(
        _("delivery mode"),
        max_length=20,
        default=DeliveryMode.INSTANT.value,
        help_text=_("'instant' fires notifiers per hit; 'digest' batches on a cadence"),
    )
    last_digest_at = models.DateTimeField(_("last digest at"), null=True, blank=True)
    next_digest_at = models.DateTimeField(_("next digest at"), null=True, blank=True)

    data = models.JSONField(
        _("data"),
        default=dict,
        help_text=_("Typed config blob, validated per `kind` (see listeners.registry)"),
    )

    class Meta:
        verbose_name = _("listener")
        verbose_name_plural = _("listeners")
        indexes = [
            models.Index(fields=["account_id"]),
            models.Index(fields=["is_active", "next_poll_at"]),
            models.Index(fields=["is_active", "delivery_mode", "next_digest_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"
