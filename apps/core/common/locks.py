"""Cache-backed try-locks.

`named_lock(name, timeout)` is the general primitive: a non-blocking
mutex keyed by an opaque name. Built on `cache.add` (atomic). Yields
True iff acquired; caller decides whether to skip, retry, or 409.

The listener-specific wrappers (`poll_lock` / `digest_lock`) and the
refresh-rotation wrapper (`refresh_token_lock`) are thin shims that
just pick the cache key and timeout for their scope.
"""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from django.conf import settings
from django.core.cache import cache


@contextmanager
def named_lock(*, name: str, timeout: int) -> Iterator[bool]:
    """Try-lock keyed by `name`. Yields True iff acquired.

    On release, only deletes the cache key if we're still the owner,
    guards against the case where our work outran `timeout`, the key
    auto-expired, another process re-acquired with a fresh token, and
    our stale `cache.delete` would otherwise clobber theirs.
    """
    token = uuid.uuid4().hex
    acquired = cache.add(name, token, timeout=timeout)
    try:
        yield bool(acquired)
    finally:
        if acquired and cache.get(name) == token:
            cache.delete(name)


def _listener_lock_name(listener_id: str, scope: str) -> str:
    return f"listener_lock:{listener_id}:{scope}"


def poll_lock(listener_id: str) -> AbstractContextManager[bool]:
    """Lock a listener's poll cycle. Yields True iff acquired."""
    return named_lock(
        name=_listener_lock_name(listener_id, "poll"),
        timeout=settings.POLL_LOCK_TIMEOUT_SECONDS,
    )


def digest_lock(listener_id: str) -> AbstractContextManager[bool]:
    """Lock a listener's digest cycle. Yields True iff acquired."""
    return named_lock(
        name=_listener_lock_name(listener_id, "digest"),
        timeout=settings.DIGEST_LOCK_TIMEOUT_SECONDS,
    )


def feed_set_lock(feed_id: str) -> AbstractContextManager[bool]:
    """Serialize concurrent `SourceService.set_sources` on one feed.

    Two operators racing `magpie feed set-sources` on the same feed
    each snapshot the existing rows independently and compute
    `removed = existing - desired`. The loser's removed set can drop
    rows the winner just added or kept, and both report success. The
    lock collapses the race to a single ordered apply; the loser
    sees a clean retry-friendly error."""
    return named_lock(
        name=f"feed_set_lock:{feed_id}",
        timeout=settings.FEED_SET_LOCK_TIMEOUT_SECONDS,
    )


def refresh_token_lock(refresh_token: str) -> AbstractContextManager[bool]:
    """Serialize concurrent refresh-token rotations for a single token.

    Hashed so the raw token value never appears as a cache key (db cache
    rows are visible to anyone with table access). Short failsafe TTL
    since the rotation critical section is a single read + revoke +
    mint, measured in milliseconds.
    """
    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:32]
    return named_lock(
        name=f"refresh_token_lock:{digest}",
        timeout=settings.REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS,
    )
