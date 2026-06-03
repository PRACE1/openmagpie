from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class WatchActionDigestWindow(BaseModel):
    """The open digest window for one digest delivery action.

    One row per (account, action). `close_at` is when the currently-open
    window closes / is due to flush (null when no window is open). The first
    arrival opens it (now + interval) and later arrivals join WITHOUT
    extending close_at (a fixed window anchored at first arrival, not a
    sliding one) ; the flush fires when `close_at <= now`, emits the action's
    pending runs as one batch, and closes it (null) so the next arrival
    reopens a fresh window.

    Membership is NOT stored here: a digest action's pending runs ARE the
    batch (the drain excludes a digest action's runs, so its only pending
    runs are the un-emitted batch). This row only coordinates the window's
    timing, under `select_for_update`.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    action_id = models.CharField(_("action id"), max_length=26)
    close_at = models.DateTimeField(_("close at"), null=True, blank=True)

    class Meta:
        verbose_name = _("watch action digest window")
        verbose_name_plural = _("watch action digest windows")
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "action_id"],
                name="uniq_watchdigestwindow_account_action",
            ),
        ]
        indexes = [
            # The flush scans for due windows across tenants (close_at <= now).
            models.Index(fields=["close_at"], name="watchdigestwin_close_at_idx"),
        ]

    def __str__(self) -> str:
        return f"digest-window {self.action_id} close_at={self.close_at} ({self.id})"
