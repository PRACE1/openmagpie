"""Cache-backed try-locks for the scheduler.

Use `poll_lock(id)` or `digest_lock(id)` to guard per-listener scheduler work
against concurrent processes. Built on `cache.add` (atomic). Not a spin lock —
returns immediately with True/False; caller decides whether to skip.

Each scope owns its own failsafe TTL (POLL_LOCK_TIMEOUT_SECONDS /
DIGEST_LOCK_TIMEOUT_SECONDS) because realistic poll and digest cycles take
very different amounts of time.
"""

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from django.conf import settings
from django.core.cache import cache


def _lock_key(listener_id: str, scope: str) -> str:
    return f"listener_lock:{listener_id}:{scope}"


@contextmanager
def _scoped_listener_lock(listener_id: str, scope: str, timeout: int) -> Iterator[bool]:
    """Internal: acquire (listener_id, scope) for `timeout` seconds max.
    Yields True iff acquired.

    On release, only deletes the cache key if we're still the owner — guards
    against the case where our work outran `timeout`, the key auto-expired,
    another process re-acquired with a fresh token, and our stale
    `cache.delete` would otherwise clobber theirs.
    """
    key = _lock_key(listener_id, scope)
    token = uuid.uuid4().hex
    acquired = cache.add(key, token, timeout=timeout)
    try:
        yield bool(acquired)
    finally:
        if acquired and cache.get(key) == token:
            cache.delete(key)


def poll_lock(listener_id: str) -> AbstractContextManager[bool]:
    """Lock a listener's poll cycle. Yields True iff acquired."""
    return _scoped_listener_lock(
        listener_id, "poll", settings.POLL_LOCK_TIMEOUT_SECONDS
    )


def digest_lock(listener_id: str) -> AbstractContextManager[bool]:
    """Lock a listener's digest cycle. Yields True iff acquired."""
    return _scoped_listener_lock(
        listener_id, "digest", settings.DIGEST_LOCK_TIMEOUT_SECONDS
    )
