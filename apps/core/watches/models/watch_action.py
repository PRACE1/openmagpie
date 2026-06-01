from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class WatchAction(BaseModel):
    """One side-effect node in a watch path's linear chain.

    ONE table, kind-discriminated (see `watches.constants.WatchActionKind`):
    semantic_filter (LLM relevance), webhook, log, digest. `config` is
    the kind-specific blob, validated server-side by a kind-keyed
    Pydantic registry (later commit) ; opaque here.

    Ordering is a dense integer `rank` (0..N-1, contiguous) WITHIN a
    path, unique on `(path_id, rank)`. Chain entry = `rank == 0`; "next"
    = `rank + 1` (plain SQL sort, no traversal). Insert/move/delete
    renumber the affected rows in a transaction. A dense sortable column
    also renders the chain for the future flow UI. `kind` is a bare
    CharField (no `choices=`) so a new kind needs no migration.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    path_id = models.CharField(_("path id"), max_length=26)
    kind = models.CharField(
        _("kind"), max_length=32, help_text=_("WatchActionKind value; selects impl + config contract")
    )
    config = models.JSONField(_("config"), default=dict, help_text=_("Kind-specific config blob, validated per kind"))
    rank = models.PositiveIntegerField(_("rank"), help_text=_("Dense 0-based position within the path"))

    class Meta:
        verbose_name = _("watch action")
        verbose_name_plural = _("watch actions")
        constraints = [
            models.UniqueConstraint(
                fields=["path_id", "rank"],
                name="uniq_watchaction_path_rank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}#{self.rank} ({self.id})"
