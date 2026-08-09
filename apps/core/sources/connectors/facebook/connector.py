"""Facebook connector: watches pages/groups and emits NewFacebookPostPayloads."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from openmagpie_schema.configs import FacebookSearchSourceSpec

from .client import FacebookClient
from .payloads import NewFacebookPostPayload


class FacebookConnector:
    """Connector that polls Facebook pages/groups via Playwright."""

    def __init__(self, spec: FacebookSearchSourceSpec) -> None:
        self.spec = spec
        self._client: FacebookClient | None = None
        self._stop = False

    async def __aenter__(self) -> FacebookConnector:
        self._client = FacebookClient(
            headless=getattr(self.spec, "headless", True),
            timeout_ms=getattr(self.spec, "timeout_ms", 30_000),
            scroll_limit=getattr(self.spec, "scroll_limit", 5),
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.__aexit__(*exc)
        self._client = None

    async def watch(self) -> AsyncIterator[NewFacebookPostPayload]:
        """Poll configured targets and yield new posts."""
        if not self._client:
            raise RuntimeError("Connector not started. Use 'async with'.")

        targets: list[str] = getattr(self.spec, "targets", []) or []
        poll_interval_s: int = getattr(self.spec, "poll_interval_s", 300)

        seen: set[str] = set()

        while not self._stop:
            for target in targets:
                try:
                    posts = await self._client.fetch_page_posts(target)
                except Exception:
                    continue

                for post in posts:
                    if post.external_id not in seen:
                        seen.add(post.external_id)
                        yield post

            await asyncio.sleep(poll_interval_s)

    def stop(self) -> None:
        """Signal the watch loop to exit."""
        self._stop = True

    @classmethod
    def from_config(cls, raw: dict) -> FacebookConnector:
        """Factory from a plain dict (e.g., loaded from YAML/JSON)."""
        spec = FacebookSearchSourceSpec.model_validate(raw)
        return cls(spec)
