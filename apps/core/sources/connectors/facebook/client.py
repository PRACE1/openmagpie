"""Facebook client: Playwright-based scraping for pages and groups."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from .payloads import NewFacebookPostPayload

FB_BASE_URL = "https://facebook.com"


class FacebookClient:
    """Async Playwright client for observing Facebook posts."""

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
        scroll_limit: int = 5,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.scroll_limit = scroll_limit
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> FacebookClient:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.0"
            ),
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_page(self) -> Page:
        if not self._context:
            raise RuntimeError("Client not started. Use 'async with'.")
        page = await self._context.new_page()
        page.set_default_timeout(self.timeout_ms)
        return page

    @staticmethod
    def _extract_post_id(href: str) -> str:
        """Best-effort post ID extraction from a Facebook URL."""
        patterns = [
            r"/posts/(\d+)",
            r"/photos/[a-z.]*/(\d+)",
            r"/videos/[a-z.]*/(\d+)",
            r"story_fbid=(\d+)",
            r"/groups/[^/]+/posts/(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, href)
            if m:
                return m.group(1)
        nums = re.findall(r"\d+", href)
        return nums[-1] if nums else ""

    @staticmethod
    def _parse_count(text: str | None) -> int | None:
        """Parse '1.2K', '3M', '42' into int or None."""
        if not text:
            return None
        text = text.strip().lower().replace(",", "")
        multipliers = {"k": 1_000, "m": 1_000_000}
        for suffix, mult in multipliers.items():
            if suffix in text:
                try:
                    return int(float(text.replace(suffix, "")) * mult)
                except ValueError:
                    return None
        try:
            return int(text)
        except ValueError:
            return None

    async def fetch_page_posts(self, page_url: str) -> list[NewFacebookPostPayload]:
        """Scroll a public Facebook page and extract post payloads."""
        page = await self._new_page()
        try:
            await page.goto(page_url, wait_until="networkidle")
            try:
                await page.click(
                    '[data-testid="cookie-policy-manage-dialog-accept-button"]',
                    timeout=5_000,
                )
            except Exception:
                pass

            posts: list[NewFacebookPostPayload] = []
            seen_ids: set[str] = set()

            for _ in range(self.scroll_limit):
                article_nodes = await page.query_selector_all('div[role="article"]')
                for node in article_nodes:
                    payload = await self._extract_post(node, page_url)
                    if payload and payload.external_id not in seen_ids:
                        seen_ids.add(payload.external_id)
                        posts.append(payload)

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

            return posts
        finally:
            await page.close()

    async def _extract_post(
        self, node: Any, page_url: str
    ) -> NewFacebookPostPayload | None:
        """Extract a single post from a DOM node."""
        try:
            author_el = await node.query_selector(
                "h3 a, h4 a, strong a, span a[href*='/']"
            )
            author = await author_el.inner_text() if author_el else ""
            author_href = await author_el.get_attribute("href") if author_el else ""
            author_id = author_href.strip("/").split("/")[-1] if author_href else ""

            content_el = await node.query_selector(
                'div[data-ad-preview="message"], span[dir="auto"]'
            )
            content = await content_el.inner_text() if content_el else ""

            link_el = await node.query_selector(
                "a[href*='/posts/'], a[href*='story_fbid=']"
            )
            href = await link_el.get_attribute("href") if link_el else ""
            post_id = self._extract_post_id(href)
            full_url = f"{FB_BASE_URL}{href}" if href.startswith("/") else href

            likes_el = await node.query_selector(
                "span[aria-label*='Like'], span[aria-label*='like']"
            )
            comments_el = await node.query_selector(
                "span[aria-label*='Comment'], span[aria-label*='comment']"
            )
            shares_el = await node.query_selector(
                "span[aria-label*='Share'], span[aria-label*='share']"
            )

            likes = self._parse_count(
                await likes_el.inner_text() if likes_el else None
            )
            comments = self._parse_count(
                await comments_el.inner_text() if comments_el else None
            )
            shares = self._parse_count(
                await shares_el.inner_text() if shares_el else None
            )

            return NewFacebookPostPayload(
                external_id=post_id or f"unknown_{datetime.now(UTC).timestamp()}",
                kind=NewFacebookPostPayload.PAYLOAD_KIND,
                occurred_at=datetime.now(UTC),
                source="facebook_search",
                title="",
                content=content,
                url=full_url or page_url,
                author=author,
                author_id=author_id,
                lang="",
                metrics={"likes": likes, "comments": comments, "shares": shares},
            )
        except Exception:
            return None

    async def fetch_group_posts(self, group_url: str) -> list[NewFacebookPostPayload]:
        """Alias for fetch_page_posts — group feeds are structurally similar."""
        return await self.fetch_page_posts(group_url)
