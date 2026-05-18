from common.models import BaseModel
from django.db import models
from django.utils.translation import gettext_lazy as _

from ..constants import (
    PROFILE_ROLE_CHOICES,
    PROFILE_ROLE_USER,
    PROFILE_STATUS_CHOICES,
    PROFILE_STATUS_PENDING,
)


class Account(BaseModel):
    """A tenant, every domain row is scoped to one Account via account_id."""

    name = models.CharField(
        _("name"), max_length=255, help_text=_("Name of the account")
    )
    is_service_account = models.BooleanField(
        _("service account"),
        default=False,
        help_text=_(
            "Internal service account (hidden from frontend, no password login)"
        ),
    )

    class Meta:
        verbose_name = _("account")
        verbose_name_plural = _("accounts")

    def __str__(self) -> str:
        return str(self.name)


class UserProfile(BaseModel):
    """Joins a User to an Account with role/status. Uses char pointers, no FKs."""

    user_id = models.CharField(
        _("user id"),
        max_length=26,
        help_text=_("The ID of the user this profile belongs to"),
    )
    account_id = models.CharField(
        _("account id"),
        max_length=26,
        help_text=_("The ID of the account this profile belongs to"),
    )
    is_primary = models.BooleanField(
        _("is primary"),
        default=False,
        help_text=_("Whether this is the primary profile for the user"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=PROFILE_STATUS_CHOICES,
        default=PROFILE_STATUS_PENDING,
        help_text=_("Status of the user's membership in the account"),
    )
    role = models.CharField(
        _("role"),
        max_length=20,
        choices=PROFILE_ROLE_CHOICES,
        default=PROFILE_ROLE_USER,
        help_text=_("Role of the user in the account"),
    )

    class Meta:
        verbose_name = _("user profile")
        verbose_name_plural = _("user profiles")
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "account_id"],
                name="unique_user_account_profile",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} - {self.account_id}"
