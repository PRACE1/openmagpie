from common.models import BaseModel
from django.db import models
from django.utils.translation import gettext_lazy as _


class Event(BaseModel):
    """A persisted hit. Minimal common fields + typed `data` blob.

    Each Event is owned by exactly one Listener (the one whose engine judged the
    observation relevant). Two listeners hitting the same external observation
    produce two Event rows, each can have its own delivery state.
    """

    user_id = models.CharField(_("user id"), max_length=26)
    account_id = models.CharField(_("account id"), max_length=26)
    listener_id = models.CharField(_("listener id"), max_length=26)
    source = models.CharField(
        _("source"),
        max_length=32,
        help_text=_("Connector kind, e.g. 'reddit_subreddit'"),
    )
    kind = models.CharField(
        _("kind"),
        max_length=32,
        help_text=_("Observation EVENT_KIND, e.g. 'new_post'"),
    )
    external_id = models.CharField(_("external id"), max_length=255)
    occurred_at = models.DateTimeField(_("occurred at"))
    delivered_at = models.DateTimeField(
        _("delivered at"),
        null=True,
        blank=True,
        help_text=_("Null = pending delivery. Set when all notifiers succeed."),
    )
    data = models.JSONField(
        _("data"), default=dict, help_text=_("Full Observation.model_dump()")
    )

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        constraints = [
            models.UniqueConstraint(
                fields=["listener_id", "source", "external_id"],
                name="unique_event_per_listener_source",
            ),
        ]
        indexes = [
            models.Index(fields=["listener_id", "delivered_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.source}:{self.kind} {self.external_id}"
