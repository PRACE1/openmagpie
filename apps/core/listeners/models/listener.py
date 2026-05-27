from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class Listener(BaseModel):
    """A single thing the user is listening for. The unit of attention in OpenMagpie.

    A listener is an ATTENTION over a Feed: it subscribes to a Feed (by id, in
    `data`) and judges every item that Feed produces (across all its streams)
    with its engine + instructions. It does NOT own streams or a poll cadence
    (the Feed does). Listener-kind-specific config (feed_id, engine, notifiers,
    ...) lives inside `data`, validated by the Pydantic class registered for
    this `kind` in `listeners.registry`.
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

    # Judgment cursor: the highest FeedItem id (ULID) this listener has
    # already considered. judge_pending processes items with id > this and
    # advances it, so misses aren't re-judged (re-spending LLM tokens). The
    # FEED owns polling; the listener owns its own judgment progress.
    last_judged_item_id = models.CharField(_("last judged item id"), max_length=26, default="", blank=True)

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
            models.Index(fields=["is_active", "delivery_mode", "next_digest_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"
