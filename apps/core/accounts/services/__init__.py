"""accounts.services, public surface.

All ORM access to User / Account / UserProfile goes through these
service classes. Other apps must not import the models directly.

    from accounts.services import UserService, AccountService, UserProfileService

    # System-level (no account context yet, e.g. signup):
    user = UserService.Global.create(email=email, password=password)
    account = AccountService.Global.create(name=email)
    UserProfileService.Global.bind_owner(user_id=str(user.id), account_id=str(account.id))

    # Cross-tenant lookups:
    aid = AccountService.Global.primary_account_id_for(user_id=str(user.id))

    # Account-scoped (instance) operations exist for completeness but are
    # currently thin; add methods as scoped read/write needs show up.
"""

from .accounts import AccountService
from .profiles import UserProfileService
from .users import UserService

__all__ = ["AccountService", "UserProfileService", "UserService"]
