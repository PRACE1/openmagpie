from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class WatchPath(BaseModel):
    """One linear chain of Actions under a Watch.

    A Watch has one or more paths; v1 creates exactly one and points
    `Watch.initial_path_id` at it. The seam for future A/B / parallel
    flows (a second path is a pure add, no model change). Actions belong
    to a path and are ordered by `Action.rank` within it.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)

    class Meta:
        verbose_name = _("watch path")
        verbose_name_plural = _("watch paths")
        indexes = [
            models.Index(fields=["account_id", "watch_id"], name="watchpath_acct_watch_idx"),
        ]

    def __str__(self) -> str:
        return f"path of {self.watch_id} ({self.id})"
