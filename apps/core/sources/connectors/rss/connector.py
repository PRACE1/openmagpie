"""Generic RSS / Atom connector.

One GET per cycle (RSS feeds don't paginate), parsed by `feedparser`,
yielding `RssEntryObservation` for entries with `occurred_at > since`.
Unlike Reddit's connector this works against any feed URL ; the per-
publisher quirks (which key holds the body, which holds the author)
are absorbed by the `field_map` override threaded through from the
Source row + feed default."""

import ipaddress
import logging
import socket
from collections.abc import Iterator
from datetime import datetime

import feedparser
import httpx
from django.conf import settings

from events.observations import Observation
from events.registry import register
from openmagpie_schema.configs import RssSourceSpec

from ..base import BaseConnector, ConnectorParseError
from .observations import RssEntryObservation

logger = logging.getLogger("sources")

# Polite default UA ; some publishers 403 the bare `python-httpx/...` UA.
# Identify the project so a publisher can correlate traffic if they look.
RSS_USER_AGENT = "openmagpie-rss/1.0 (+https://github.com/obris-dev/openmagpie)"

# Cap how many bytes we accumulate from a single feed in one cycle.
# RSS feeds are typically <100KB; a >5MB body is either a misconfigured
# endpoint (serving the full archive) or a hostile target. Streamed +
# checked per chunk so we never buffer past the cap (unlike a
# `response.content`-then-len check, which materializes the full body
# before deciding).
MAX_BODY_BYTES = 5 * 1024 * 1024


def _block_private_ip(host: str, *, url: httpx.URL) -> None:
    """Raise `ConnectorParseError` if `host` resolves to a private /
    loopback / link-local / multicast / reserved address and the
    SOURCE_BLOCK_PRIVATE_IPS setting is on. Catches both IP-literal
    URLs (no DNS round-trip) and hostname URLs (one getaddrinfo). The
    schema validator rejected the no-host case at create."""
    try:
        ip = ipaddress.ip_address(host)
        candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ConnectorParseError(f"rss: DNS resolution failed for {host!r}: {exc}") from exc
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
    for ip in candidates:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ConnectorParseError(
                f"rss: blocked URL {url} (host {host!r} resolves to {ip}; SOURCE_BLOCK_PRIVATE_IPS is set)"
            )


def _validate_request_url(request: httpx.Request) -> None:
    """httpx request hook ; runs for the initial GET AND for every
    redirect target so a public hostname that 302s to an internal
    address is rejected before httpx makes the inner fetch. No-op if
    the SSRF setting is off."""
    if not settings.SOURCE_BLOCK_PRIVATE_IPS:
        return
    host = request.url.host
    if not host:
        return
    _block_private_ip(host, url=request.url)


class RssConnector(BaseConnector):
    """Polls a single RSS or Atom feed URL.

    Live-mode semantics: every cycle yields entries newer than `since`,
    where `since` is the Source row's `last_event_at`. `feeds.policy`
    stamps `last_event_at = now()` at save time so the first cycle just
    returns whatever's been published since the source was created.

    Backfill is opt-in at source creation via `SourceInput.last_event_at`
    (the operator can pin a past datetime). There's no walk-the-archive
    mode ; RSS feeds typically don't expose history past the latest N
    items anyway, so a true backfill needs a different mechanism
    (sitemap, archive-only feeds, ...) that doesn't belong in this
    connector.

    `field_map` recognised keys: `external_id`, `title`, `url`,
    `content`, `author`, `published`. Each is the feedparser-entry
    key to read INSTEAD of the canonical default (e.g. `entry.id` for
    external_id). Most feeds need no overrides ; feedparser normalizes
    RSS / Atom / dc:* differences itself. Unknown override keys are
    read from the entry as-is so a publisher with a namespaced field
    can use `{"author": "itunes_author"}` without a connector change.
    Unknown canonical names in `field_map` are silently dropped."""

    kind = RssSourceSpec.SOURCE_KIND
    observations: list[type[Observation]] = [RssEntryObservation]

    def poll(
        self,
        spec: RssSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> Iterator[RssEntryObservation]:
        field_map = field_map or {}

        # Client carries the per-request hook so every redirect target
        # is re-checked under SOURCE_BLOCK_PRIVATE_IPS (a 302 from a
        # public host to 169.254.169.254 raises before httpx fetches
        # the inner target). Body is streamed + capped per chunk so the
        # MAX_BODY_BYTES gate runs before the bytes are buffered.
        body = bytearray()
        with (
            httpx.Client(
                event_hooks={"request": [_validate_request_url]},
                follow_redirects=True,
                timeout=15.0,
                headers={"User-Agent": RSS_USER_AGENT},
            ) as client,
            client.stream("GET", spec.url) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ConnectorParseError(f"rss feed {spec.url} exceeded {MAX_BODY_BYTES}-byte cap mid-stream")

        parsed = feedparser.parse(bytes(body))
        # The "is this even a feed?" gate keys on `parsed.version`. Real
        # feeds always set `version` (`'rss20'`, `'atom10'`, ...) ; HTML
        # rate-limit / block pages parse as bozo=False, version=''
        # which would otherwise silently land as "no new posts" and
        # never surface the upstream block.
        #
        # Bozo is intentionally NOT a fail trigger here. feedparser
        # raises bozo=1 with a SAX exception for non-fatal quirks like
        # an undeclared `dc:` namespace prefix (very common: most RSS
        # 2.0 feeds use `<dc:date>` without declaring xmlns:dc), and a
        # truncated body raises the same exception class. Failing on
        # bozo would mean a valid feed with a warning + a genuinely
        # empty cycle gets thrown out, AND we couldn't reliably
        # discriminate from a hard parse failure without matching on
        # exception messages. Trade-off: truncated XML returning zero
        # recovered entries reads as "empty cycle" instead of an
        # error ; next poll picks it up when the publisher recovers.
        if not parsed.entries and not parsed.version:
            raise ConnectorParseError(
                f"rss feed {spec.url} returned an unparseable body (no feed format detected; "
                "version='', 0 entries) ; likely an HTML block / rate-limit page or non-feed URL"
            )

        for entry in parsed.entries:
            obs, missing = RssEntryObservation.from_feedparser_entry(entry, spec, field_map)
            if obs is None:
                # Named so the operator can spot which `field_map`
                # override the publisher needs (e.g. "missing
                # external_id" on a feed that puts the id in
                # `<media:content url=...>` -> set `field_map:
                # external_id: media_content`). DEBUG by default
                # because well-behaved feeds shouldn't trip this;
                # WARN here would spam production logs for a
                # publisher who's missing one row's pubDate.
                logger.debug(
                    "rss: skipped entry on %s (missing %s): %r",
                    spec.url,
                    missing,
                    entry.get("title", "<no title>"),
                )
                continue
            if since is not None and obs.occurred_at <= since:
                continue
            yield obs


# Register observations for hydration of Event.data, single source of truth via the class attrs.
register(RssConnector.kind, RssConnector.observations)
