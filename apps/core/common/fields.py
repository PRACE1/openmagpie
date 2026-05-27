from datetime import datetime

import ulid
from django.db import models
from django.utils.translation import gettext_lazy as _


def min_ulid_at(dt: datetime) -> str:
    """Smallest ULID whose timestamp is `dt` (ms-truncated).

    For id-range cutoffs equivalent to a `created_at` cutoff: ULIDs are
    lex-sortable and time-monotonic at ms resolution, so `id < min_ulid_at(dt)`
    selects exactly rows whose ULID-ms < dt-ms. A row whose ULID-ms equals
    dt-ms survives this cycle (random bits > all-zeros) and is caught next
    cycle — boundary-safe, never permanently missed.
    """
    return ulid.encode_time(int(dt.timestamp() * 1000), 10) + "0" * 16


class ULIDField(models.CharField):
    """26-char Crockford-Base32 ULID, auto-generated on save if blank."""

    description = _("ULID (Universally Unique Lexicographically Sortable Identifier)")

    def __init__(
        self,
        verbose_name: str | None = None,
        *,
        primary_key: bool = False,
        editable: bool = False,
        unique: bool = False,
        db_index: bool = False,
        null: bool = False,
        blank: bool = False,
        help_text: str = "",
        serialize: bool = True,
        max_length: int = 26,
    ) -> None:
        if max_length != 26:
            raise ValueError("ULIDField max_length is fixed at 26")
        super().__init__(
            verbose_name=verbose_name,
            max_length=26,
            primary_key=primary_key,
            editable=editable,
            unique=unique,
            db_index=db_index,
            null=null,
            blank=blank,
            help_text=help_text,
            serialize=serialize,
        )

    def pre_save(self, model_instance: models.Model, add: bool) -> str:
        value: str = getattr(model_instance, self.attname)
        if not value:
            value = str(ulid.ulid())
            setattr(model_instance, self.attname, value)
        return value
