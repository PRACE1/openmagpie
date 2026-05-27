"""Tiny human-readable formatters for management-command output.

`humanize_seconds(s)` collapses raw second counts into the smallest
unit a human reads naturally: under a minute stays as seconds, under
an hour reads as "Nm Xs", an hour+ reads as "Nh Xm". Used by progress
ETAs in judge cycles. "~513s left" doesn't tell anyone they're 8.5
minutes from done; "~8m 33s left" does.

`ellipsize(text, max_len)` truncates with a trailing `…` and strips
whitespace from the cut point so the ellipsis sits flush against the
last word, not after a stray space ("foo bar baz …" → "foo bar baz…").
"""

from __future__ import annotations


def humanize_seconds(seconds: int) -> str:
    """Format a non-negative integer second count for human display.

    Boundary behavior:
      - 0..59: `42s`
      - 60..3599: `1m`, `1m 30s`, `8m 33s`
      - 3600+: `1h`, `1h 5m`, `2h 30m` (seconds dropped at this scale)
    """
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, rem = divmod(seconds, 60)
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def ellipsize(text: str, max_len: int) -> str:
    """Truncate `text` to fit within `max_len` characters, appending
    `…` when a cut happens. Whitespace at the cut point is stripped so
    the ellipsis sits flush against the last word.

    Returns `text` unchanged when it already fits. `max_len` is the
    total budget including the ellipsis char, so `ellipsize("foo bar baz", 7)`
    returns `"foo b…"` (6-char prefix + 1-char ellipsis = 7).
    """
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rstrip()
    return f"{cut}…"
