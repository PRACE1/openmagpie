from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class WatchFeed(BaseModel):
    """A Watch's subscription to one Feed, plus its per-feed progress.

    Watches subscribe to Feeds, never the reverse ; a Feed knows nothing
    about its watchers. v1 creates exactly one row per watch (multi-feed
    fan-in is a pure add later). One row per (watch, feed).

    Carries BOTH concerns on the same 1:1 key:
      - subscription (operator config): which feed this watch watches.
      - `last_item_id` (runtime watermark): highest FeedItem ULID this
        (watch, feed) has triggered on. System-written each trigger
        cycle ; the trigger scans `FeedItem` with id greater than it,
        enqueues a run per item, then advances it. Blank before the
        first cycle ; the scan then starts from the feed's head per the
        watch's create-time policy. The wire shape OMITS this field
        (it's internal progress, not operator config).

    The watermark living here (not on the feed) is why the feed prune
    never reaches across into watches ; the cursor responsibility the
    old Listener carried implicitly now sits on the watches side.

    `account_id` is denormalized for direct scoped queries.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)
    feed_id = models.CharField(_("feed id"), max_length=26)
    last_item_id = models.CharField(_("last item id"), max_length=26, blank=True, default="")

    class Meta:
        verbose_name = _("watch feed")
        verbose_name_plural = _("watch feeds")
        constraints = [
            models.UniqueConstraint(
                fields=["watch_id", "feed_id"],
                name="uniq_watchfeed_watch_feed",
            ),
        ]
        indexes = [
            # "feeds this watch subscribes to" ; the unique constraint's
            # left prefix already covers watch_id lookups.
            models.Index(fields=["account_id", "feed_id"], name="watchfeed_acct_feed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.watch_id}->{self.feed_id} ({self.id})"
