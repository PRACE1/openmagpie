"""Cache-backed try-locks.

`named_lock(name, timeout)` is the general primitive: a non-blocking
mutex keyed by an opaque name. Built on `cache.add` (atomic). Yields
True iff acquired; caller decides whether to skip, retry, or 409.

The feed poll wrapper (`poll_lock`), the feed set-sources wrapper
(`feed_set_lock`), and the refresh-rotation wrapper (`refresh_token_lock`)
are thin shims that just pick the cache key and timeout for their scope.
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


def poll_lock(feed_id: str) -> AbstractContextManager[bool]:
    """Lock a feed's poll cycle. Yields True iff acquired."""
    return named_lock(
        name=f"feed_poll_lock:{feed_id}",
        timeout=settings.POLL_LOCK_TIMEOUT_SECONDS,
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


def path_chain_lock(path_id: str) -> AbstractContextManager[bool]:
    """Serialize chain mutations on one WatchPath (add / remove / replace).

    Rank uniqueness is per-path (`unique(account_id, path_id, rank)`), so
    the path is the right grain. Two concurrent add/removes each snapshot
    the chain, recompute dense ranks, and write back ; without serializing
    they collide on the rank constraint (or interleave into a gapped
    chain). Locking the PATH (not its action rows) also covers the
    add-first-action race, where a row-level lock would find nothing to
    lock. The loser gets a clean retry-friendly error."""
    return named_lock(
        name=f"path_chain_lock:{path_id}",
        timeout=settings.PATH_CHAIN_LOCK_TIMEOUT_SECONDS,
    )


def job_lock(name: str) -> AbstractContextManager[bool]:
    """Single-flight a scheduled job (a management command) by name.

    Skip-if-held: yields True iff acquired ; a run that finds a prior pass
    still going gets False and should log + skip rather than pile up behind
    it. Built on `named_lock`, so release rides the same owner-token
    finally ; a normal exit, a handled exception, even SIGTERM
    (SystemExit runs finally) all free it promptly. Only a hard SIGKILL /
    power loss skips the finally ; `JOB_LOCK_TIMEOUT_SECONDS` (deliberately
    a full day) is the failsafe that eventually frees a lock orphaned that
    way, set long so a legitimately hours-long pass never expires it."""
    return named_lock(
        name=f"job_lock:{name}",
        timeout=settings.JOB_LOCK_TIMEOUT_SECONDS,
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
