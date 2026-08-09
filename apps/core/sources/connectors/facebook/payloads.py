"""Facebook payloads: a post observed via the Playwright/CDP route."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import FacebookSearchSourceSpec
from sources.payloads import SourcePayload

FB_BASE_URL = "https://facebook.com"


class NewFacebookPostPayload(SourcePayload):
    """A single Facebook post observed by a watched page/group stream."""

    PAYLOAD_KIND: ClassVar[str] = "new_facebook_post"

    author: str = ""
    author_id: str = ""
    lang: str = ""
    metrics: dict[str, int | None] = {}

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.author or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewFacebookPostPayload:
        n = variant + 1
        post_id = str(999_000_000_000_000_000 + n)
        return cls(
            external_id=post_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=FacebookSearchSourceSpec.SOURCE_KIND,
            title="",
            content=f"Example Facebook post {n}: the post text that matched this watch.",
            url=f"{FB_BASE_URL}/example_page/posts/{post_id}",
            author=f"Example Page {n}",
            author_id=f"page_{n}",
            lang="en",
            metrics={"likes": 100 + n, "comments": 20 + n, "shares": 5 + n},
        )

    @classmethod
    def from_post(cls, post: dict[str, Any], page_url: str | None = None) -> NewFacebookPostPayload:
        """Map a raw scraped post dict to a payload."""
        post_id = str(post.get("id", ""))
        author = str(post.get("author", ""))
        content = str(post.get("content", ""))
        occurred_at = post.get("timestamp")
        if not isinstance(occurred_at, datetime):
            occurred_at = datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return cls(
            external_id=post_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=FacebookSearchSourceSpec.SOURCE_KIND,
            title="",
            content=content,
            url=f"{page_url}/posts/{post_id}" if page_url and post_id else (page_url or ""),
            author=author,
            author_id=str(post.get("author_id", "")),
            lang=str(post.get("lang", "")),
            metrics={
                "likes": post.get("likes"),
                "comments": post.get("comments"),
                "shares": post.get("shares"),
            },
        )
