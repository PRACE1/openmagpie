"""Facebook connector: polls pages/groups and yields NewFacebookPostPayloads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import ClassVar

from openmagpie_schema.configs import FacebookSearchSourceSpec
from sources.connectors.base import BaseConnector
from sources.payloads import SourcePayload

from .client import FacebookClient
from .payloads import NewFacebookPostPayload


class FacebookConnector(BaseConnector[FacebookSearchSourceSpec]):
    """Connector that polls Facebook pages/groups via Playwright."""

    kind: ClassVar[str] = "facebook_search"
    payloads: ClassVar[list[type[SourcePayload]]] = [NewFacebookPostPayload]

    def poll(
        self,
        spec: FacebookSearchSourceSpec,
        since: datetime | None = None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        """Fetch posts from Facebook pages/groups newer than `since`."""
        targets: list[str] = getattr(spec, "targets", []) or []
        if not targets:
            page_url = getattr(spec, "page_url", None)
            if page_url:
                targets = [page_url]

        async def _fetch_all() -> list[NewFacebookPostPayload]:
            posts: list[NewFacebookPostPayload] = []
            async with FacebookClient(
                headless=getattr(spec, "headless", True),
                timeout_ms=getattr(spec, "timeout_ms", 30_000),
                scroll_limit=getattr(spec, "scroll_limit", 5),
            ) as client:
                for target in targets:
                    try:
                        fetched = await client.fetch_page_posts(target)
                        posts.extend(fetched)
                    except Exception:
                        continue
                    if heartbeat:
                        heartbeat()
            return posts

        try:
            loop = asyncio.get_running_loop()
            all_posts = loop.run_until_complete(_fetch_all())
        except RuntimeError:
            all_posts = asyncio.run(_fetch_all())

        for post in all_posts:
            if since is not None and post.occurred_at < since:
                continue
            yield post
