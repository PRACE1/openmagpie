"""Auth API resource client + response models.

`AuthApi` wraps the transport client with typed entrypoints for every
`/v1/auth/*` endpoint. Accessed via `Api.auth` (see `api/__init__.py`),
so call sites read `ac.api.auth.me()` rather than threading the raw
http client through each handler.

Models match the Django response shapes (see `core/auth_api/`); update
both sides together when the contract changes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .. import routes
from ..constants import BEARER_TOKEN_TYPE, DeviceSessionStatus
from ..http import MagpieClient, client_info


class AuthUser(BaseModel):
    id: str
    email: str
    account_id: str | None = None
    created_at: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: Literal[BEARER_TOKEN_TYPE]
    user: AuthUser


class DeviceSessionCreated(BaseModel):
    session_id: str
    authorize_url: str
    user_code: str
    # Bearer credential the CLI presents on each poll. Returned by the
    # server ONCE at create time; never persisted to disk. Identifies
    # *this CLI process* as the legitimate poller for this session_id.
    device_secret: str
    expires_in: int


class DeviceSessionPending(BaseModel):
    status: Literal[DeviceSessionStatus.PENDING]


class DeviceSessionExpired(BaseModel):
    status: Literal[DeviceSessionStatus.EXPIRED]


class DeviceSessionCompleted(TokenPair):
    """Completed bag is a TokenPair plus the status tag for the union
    discriminator. Inheriting keeps `AppContext.sign_in(bundle)` polymorphic
    across the device-flow completion path and any future direct-login path.
    """

    status: Literal[DeviceSessionStatus.COMPLETED]


DeviceSessionPoll = DeviceSessionPending | DeviceSessionCompleted | DeviceSessionExpired


class TokensApi:
    """Resource client for `/v1/auth/tokens/*`, bearer lifecycle."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def revoke(self) -> None:
        """Invalidate the current bearer token server-side. Best-effort:
        the server returns 200 even if the token is already revoked, so
        this won't raise except on transport errors.
        """
        self._http.post(routes.auth.tokens.revoke)


class AuthApi:
    """Resource client for `/v1/auth/*`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http
        self.tokens = TokensApi(http)

    def create_device_session(self) -> DeviceSessionCreated:
        raw = self._http.post(
            routes.auth.device_sessions,
            with_auth=False,
            # Structured CLI identity. Server stores it under `initiator`
            # in the cache bag; the authorize page renders the fields
            # directly with no User-Agent parsing.
            json_body={"client": client_info()},
        )
        return DeviceSessionCreated.model_validate(raw)

    def poll_device_session(
        self, session_id: str, *, device_secret: str
    ) -> DeviceSessionPoll:
        raw = self._http.get(
            routes.auth.device_session(session_id),
            headers={"X-Device-Secret": device_secret},
        )
        status = raw.get("status")
        match status:
            case DeviceSessionStatus.COMPLETED:
                return DeviceSessionCompleted.model_validate(raw)
            case DeviceSessionStatus.EXPIRED:
                return DeviceSessionExpired.model_validate(raw)
            case DeviceSessionStatus.PENDING:
                return DeviceSessionPending.model_validate(raw)
            case _:
                # An unknown status means the server contract changed
                # out from under us. Failing loud beats quietly treating
                # it as pending and looping forever.
                raise RuntimeError(
                    f"unknown device-session status from server: {status!r}"
                )

    def me(self) -> AuthUser:
        raw = self._http.get(routes.auth.me)
        return AuthUser.model_validate(raw)
