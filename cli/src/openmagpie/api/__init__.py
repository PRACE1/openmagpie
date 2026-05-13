"""Top-level API coordinator.

`Api` owns the underlying MagpieClient and exposes resource sub-clients
lazily via `@cached_property`. Call sites read like an SDK:

    ac.api.auth.me()
    ac.api.auth.create_device_session()
    ac.api.listeners.list()   # when listeners ships

Adding a resource = one new file in `api/`, one cached_property here.
"""

from __future__ import annotations

from functools import cached_property

from ..http import MagpieClient
from .auth import AuthApi


class Api:
    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    @cached_property
    def auth(self) -> AuthApi:
        return AuthApi(self._http)


__all__ = ["Api", "AuthApi"]
