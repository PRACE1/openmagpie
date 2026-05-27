from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class FeedItem(BaseModel):
    """One item a Feed pulled from a stream — the persisted, browsable log.

    Holds ALL polled items (not hit-only): this is the "sort by new and
    go" surface and the input Listeners judge. `data` is the connector
    Observation's dump. Retention-windowed (pruned in the poll cycle).

    Ordering is by the ULID pk (`-id` = ingestion order). Hot queries
    (judgment scan, retention prune, recent-items listing) all share the
    shape `account_id= + feed_id= + id-range`, so a `(account_id, feed_id,
    id)` index covers them; prune uses `min_ulid_at(cutoff)` instead of a
    `created_at` filter.

    `account_id` is denormalized from Feed so every read query filters at
    the DB on the account explicitly — no trusting "feed_id implies
    account_id." Set at record_items time from `feed.account_id`.
    """

    # Denormalized from Feed.account_id; set at record_items time so every
    # read query explicitly scopes by account at the DB layer.
    account_id = models.CharField(_("account id"), max_length=26)
    feed_id = models.CharField(_("feed id"), max_length=26)
    # Connector kind that produced this item (e.g. "reddit_subreddit").
    source = models.CharField(_("source"), max_length=64)
    # The item's identity within the source (e.g. Reddit post id).
    external_id = models.CharField(_("external id"), max_length=255)
    # Display label of the producing stream (e.g. "r/foo"), set at record
    # time from the StreamSpec's display(). Cheap per-row attribution for
    # the feed-view UI; no role in filtering (listeners see every stream
    # in their Feed).
    stream_label = models.CharField(_("stream label"), max_length=255, default="")
    # Source timestamp (when the item was created at the source), for
    # display. NOT the ordering key (that's the ULID pk). Unindexed.
    occurred_at = models.DateTimeField(_("occurred at"), null=True, blank=True)
    data = models.JSONField(_("data"), default=dict, help_text=_("Connector Observation dump"))

    class Meta:
        verbose_name = _("feed item")
        verbose_name_plural = _("feed items")
        constraints = [
            models.UniqueConstraint(
                fields=["feed_id", "source", "external_id"],
                name="uniq_feeditem_per_feed_source_external",
            ),
        ]
        indexes = [
            models.Index(fields=["account_id", "feed_id", "id"], name="feeditem_acct_feed_id_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id} ({self.feed_id})"
