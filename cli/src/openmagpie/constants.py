"""Wire-level constants shared with the server API contract.

The status values match the strings the server emits in device-session
poll responses. Keep this file in lockstep with
`core/auth_api/constants.py`; they're the same wire contract from two
sides.
"""

from __future__ import annotations

from enum import StrEnum


class DeviceSessionStatus(StrEnum):
    """Status values returned by GET /v1/auth/device-sessions/{id}."""

    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


# HTTP transport constants.
AUTHORIZATION_HEADER = "Authorization"
BEARER_SCHEME = "Bearer"

# OAuth2 token_type value as emitted by the server (RFC 6750).
BEARER_TOKEN_TYPE = "Bearer"
