from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class Watch(BaseModel):
    """The DAG container an operator builds: a Feed-subscription set + a
    chain of Actions that run over each new item.

    A Watch SUBSCRIBES to one or more Feeds via `WatchFeed` rows (v1
    assumes exactly one). Its action chain lives on a `WatchPath`;
    `initial_path_id` points at the path the trigger starts items down.
    v1 creates exactly one path per watch ; the path layer is the seam
    for future A/B / parallel flows.

    Account-scoped like Feed (`account_id` filters every read);
    `user_id` is the creator, audit/display only.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    user_id = models.CharField(_("user id"), max_length=26)
    name = models.CharField(_("name"), max_length=255, help_text=_("Short label"))
    is_active = models.BooleanField(_("is active"), default=True)
    # Entry pointer: the WatchPath the trigger starts items down. Blank
    # only in the window between Watch row creation and its first path
    # (both created in one transaction by the create service).
    initial_path_id = models.CharField(_("initial path id"), max_length=26, blank=True, default="")

    class Meta:
        verbose_name = _("watch")
        verbose_name_plural = _("watches")
        indexes = [
            models.Index(fields=["account_id", "id"], name="watch_acct_id_idx"),
            # The trigger pass scans active watches across accounts.
            models.Index(fields=["is_active"], name="watch_is_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"
