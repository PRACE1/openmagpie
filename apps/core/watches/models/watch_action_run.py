from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class WatchActionRun(BaseModel):
    """One execution of one WatchAction against one FeedItem ; the
    stateful audit row of the whole pipeline.

    ONE table, `result` is the kind-specific output blob (opaque here,
    validated per kind by the registry). `state` is a bare CharField
    over `watches.constants.WatchActionRunState` (no `choices=`): the runner
    advances the chain to `rank+1` IFF `state == SUCCEEDED`; `GATED` is
    a clean run whose result halts the chain (a filter pass=false).

    `watch_id` is denormalized for cheap watch-scoped queries; the path
    is reachable via the action. `prior_run_id` records which run queued
    this one (provenance). Idempotent on `(watch, action, feed_item)`.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)
    action_id = models.CharField(_("action id"), max_length=26)
    feed_item_id = models.CharField(_("feed item id"), max_length=26)
    state = models.CharField(
        _("state"),
        max_length=16,
        default="pending",
        help_text=_("WatchActionRunState value"),
    )
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    result = models.JSONField(_("result"), default=dict, help_text=_("Kind-specific result blob"))
    error = models.TextField(_("error"), blank=True, default="")
    # The run that queued this one (the prior action in the chain). Blank
    # for the chain's first run (queued by the trigger pass, not a run).
    prior_run_id = models.CharField(_("prior run id"), max_length=26, blank=True, default="")

    class Meta:
        verbose_name = _("watch action run")
        verbose_name_plural = _("watch action runs")
        constraints = [
            # account_id-first for scoping + index coverage; the
            # (watch, action, feed_item) triple is already globally unique
            # (idempotency key for "this action already ran on this item").
            models.UniqueConstraint(
                fields=["account_id", "watch_id", "action_id", "feed_item_id"],
                name="uniq_watchactionrun_account_watch_action_item",
            ),
        ]
        indexes = [
            # The cron drain pulls due PENDING runs ordered by schedule;
            # DELIBERATELY account-agnostic ; the drain is a Global
            # cross-tenant scan, so no account_id prefix here.
            models.Index(fields=["state", "scheduled_at"], name="watchrun_state_sched_idx"),
            # Per-action audit log (magpie watch action runs <id>).
            models.Index(fields=["account_id", "action_id", "id"], name="watchrun_acct_action_idx"),
        ]

    def __str__(self) -> str:
        return f"run {self.action_id}:{self.feed_item_id} [{self.state}] ({self.id})"
