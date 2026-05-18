"""Signup orchestration.

Creates a User, their primary Account, and the owner UserProfile
linking them. Calls into accounts.services rather than touching the
models directly (cross-app access goes through services, per
AGENTS.md).

This Operation is NOT self-wrapped in `transaction.atomic`. Callers
own the transaction boundary so that any subsequent work in the same
view (e.g. minting a token pair, building the response) rolls the
new user back on failure too. `auth_api/views.py:SignupView` wraps
the call site accordingly.

`EmailAlreadyExists` is raised when User.email collides with the
unique constraint. The view catches it and returns 409, closing the
race window where two concurrent signups both passed the optimistic
`email_exists` pre-check before either committed.
"""

from __future__ import annotations

from django.db import IntegrityError

from accounts.models.user import User
from accounts.services import AccountService, UserProfileService, UserService


class EmailAlreadyExists(Exception):
    """User.email unique-constraint collision during signup."""

    def __init__(self, email: str) -> None:
        super().__init__(email)
        self.email = email


class SignupOperation:
    def __init__(self, *, email: str, password: str) -> None:
        self.email = email
        self.password = password

    def run(self) -> User:
        try:
            user = UserService.Global.create(email=self.email, password=self.password)
        except IntegrityError as e:
            # Narrow on purpose: only this call's IntegrityError gets
            # relabeled. The downstream Account / UserProfile creates
            # are outside the try so a future unique constraint there
            # surfaces as itself instead of being mis-reported as
            # "email taken".
            raise EmailAlreadyExists(self.email) from e
        # Account.name is a display field. Default to a non-PII label;
        # the user renames their primary account from settings later.
        account = AccountService.Global.create(name="Personal")
        UserProfileService.Global.bind_owner(
            user_id=str(user.id),
            account_id=str(account.id),
        )
        return user
