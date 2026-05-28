"""Bounded retry for transient judge failures.

`with_retry(fn, *args, listener=, item_id=, **kwargs)` calls `fn` and
retries on `_TRANSIENT_ERRORS` (network blip, server hiccup) up to
`_MAX_ATTEMPTS` times with exponential backoff. After the final attempt
the raised exception propagates to the cycle-level handler in
`_operation.py`, which holds the cursor and breaks (existing behavior).

Non-transient recoverable errors (`pydantic.ValidationError` from a
shape-drifted Ollama response) are NOT in `_TRANSIENT_ERRORS`: re-asking
won't fix a contract change, so they fail fast.
"""

import logging
import time
from collections.abc import Callable

import httpx

from listeners.models import Listener

logger = logging.getLogger("listeners")

# Transient = retryable. httpx.HTTPError covers the whole network-side
# family (RemoteProtocolError, ConnectError, ReadTimeout, ...). Stdlib
# ConnectionError catches the rare case where the engine raises one
# directly instead of via httpx.
_TRANSIENT_ERRORS = (
    httpx.HTTPError,
    ConnectionError,
)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0  # 1s, 2s between the 3 attempts


def with_retry[T](
    fn: Callable[..., T],
    /,
    *args,
    listener: Listener,
    item_id: str,
    **kwargs,
) -> T:
    """Call `fn(*args, **kwargs)`; retry on transient errors with
    exponential backoff. Returns `fn`'s return value on first success.
    Raises the final exception when retries exhaust."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except _TRANSIENT_ERRORS as exc:
            if attempt >= _MAX_ATTEMPTS:
                raise
            sleep_s = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "transient judge failure (attempt %d/%d) listener=%s feed_item=%s err=%s: %s; retrying in %.1fs",
                attempt,
                _MAX_ATTEMPTS,
                listener.id,
                item_id,
                type(exc).__name__,
                exc,
                sleep_s,
            )
            time.sleep(sleep_s)
    # Unreachable: loop either returns or raises in the final attempt.
    raise RuntimeError("retry loop fell through")
