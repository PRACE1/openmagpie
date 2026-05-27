from enum import StrEnum

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class EventKind(StrEnum):
    """All known Event kinds. v1 has only HIT; add new members as kinds ship.

    The canonical vocabulary for callers (`kind=EventKind.HIT`), so we
    don't ship a separate `*_KIND` string constant per kind. Not bound
    to `Event.kind` as `choices=`: that would force a migration on every
    new member; this enum is application-layer only.
    """

    HIT = "hit"


class Event(BaseModel):
    """A persisted pipeline event. A **hit** is its v1 `kind`.

    A hit is a Listener's judgment that a FeedItem is relevant. `kind` is
    the event-type discriminator (default "hit"); forward room for
    "feed_change", "delivery", etc. — that's what makes "a hit is a type
    of event" literal.

    Each hit is owned by exactly one Listener. It keeps its own `data`
    snapshot of the FeedItem (so delivery survives FeedItem retention
    pruning). Dedup is on `(kind, listener_id, source, external_id)` —
    the source-shaped identity — so a re-emitted post after FeedItem
    retention prune can't slip past as a duplicate delivery. `feed_item_id`
    stays as provenance (which row originated this Event at hit time);
    it is not the dedup key.
    """

    user_id = models.CharField(_("user id"), max_length=26)
    account_id = models.CharField(_("account id"), max_length=26)
    listener_id = models.CharField(_("listener id"), max_length=26)
    # Event-type discriminator. NOT NULL, non-empty (enforced at the write
    # seam) — it LEADS the unique constraint, so a null/blank value would
    # defeat uniqueness via null-distinctness. No default: a forgotten `kind=`
    # arg should fail loudly, not silently miscategorize the row as a hit.
    kind = models.CharField(
        _("kind"),
        max_length=32,
        help_text=_("Event-type discriminator (v1: only 'hit'; future: 'feed_change', 'delivery', ...)"),
    )
    # Source-shaped identity of the producing item, denormalized from
    # FeedItem at persist time so dedup survives FeedItem retention prune.
    source = models.CharField(_("source"), max_length=64, default="")
    external_id = models.CharField(_("external id"), max_length=255, default="")
    # Provenance: which FeedItem row originated this Event at hit time.
    # Not part of the dedup key; the FeedItem may be pruned later.
    feed_item_id = models.CharField(_("feed item id"), max_length=26, default="")
    score = models.FloatField(
        _("score"),
        null=True,
        blank=True,
        help_text=_("Engine relevance score for a hit (null for non-hit kinds)"),
    )
    delivered_at = models.DateTimeField(
        _("delivered at"),
        null=True,
        blank=True,
        help_text=_("Null = pending delivery. Set when all notifiers succeed."),
    )
    data = models.JSONField(_("data"), default=dict, help_text=_("Deliverable FeedItem/Observation snapshot"))

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        constraints = [
            # Dedup by source-shaped identity so a post re-emitted after
            # FeedItem retention prune can't deliver twice. Kind leads for
            # forward-room when other event kinds ship.
            models.UniqueConstraint(
                fields=["kind", "listener_id", "source", "external_id"],
                name="uniq_event_kind_listener_source",
            ),
        ]
        # No separate index: the unique constraint's (kind, listener_id, ...)
        # leftmost prefix covers the hit queries. A partial index
        # WHERE delivered_at IS NULL is deferred until measured.

    def __str__(self) -> str:
        return f"{self.kind} listener={self.listener_id} item={self.feed_item_id}"
