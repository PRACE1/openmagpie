"""`magpie quickstart`: one-command path from "I cloned this" to a
working listener.

Opinionated, no template picker. Reddit subreddits only because that's
all the Reddit connector supports today. Creates one Feed + one Listener
with sensible defaults (5-min poll, 30-day retention, log notifier,
hit threshold 0.7) and shows the resulting payload sample so the
operator sees what hits will look like before any real items arrive.

Engine availability is checked up front so an "Ollama down" environment
fails BEFORE the operator types instructions, vs discovering it at
first judge cycle once polling starts. The listener pins
`engine.model` to whatever the server reports as its default so the
existing listener-config policy availability check (see
`apps/core/listeners/policy.py`) catches future drift on every save.

After creation, the operator tunes via `magpie listener edit <id>` /
`magpie feed edit <id>`; the wizard never opens an editor.
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime, timedelta

import typer

from openmagpie_schema.configs import RedditSubredditSourceSpec
from openmagpie_schema.feed import SourceInput

from .. import console
from ..api.engine import EngineStatus
from ..api.feed import FeedEnvelope
from ..api.listener import ListenerEnvelope
from ..context import AppContext, app_ctx
from ..http import ApiError
from ._shared import _handle_api_errors, _print_api_error, _print_payload_sample


@_handle_api_errors
def quickstart() -> None:
    """Interactive setup: one command to a working listener.

    Asks for a comma-separated subreddit list and the listener's
    instructions, previews the plan, then validates + creates both
    resources. Bails early on signed-out and on unreachable engine
    so the operator gets actionable errors before any state changes.
    """
    ac = app_ctx()

    if not ac.config.is_authenticated:
        console.error("Not signed in. Run `magpie auth login` first.")
        raise typer.Exit(code=1)

    console.header(
        "Welcome to OpenMagpie. Two questions to start getting notified when something you care about is posted."
    )
    typer.echo("")

    engine = _require_available_engine(ac)
    typer.echo("")

    subreddits = _prompt_subreddits()
    instructions = _prompt_instructions()
    backfill = _prompt_backfill()

    base = _slug(subreddits[0])
    feed_name = f"{base}-feed"
    listener_name = f"{base}-listener"

    typer.echo("")
    console.header("Here's the plan:")
    sources = ", ".join(f"r/{s}" for s in subreddits)
    console.kv("Watching", f"{sources} (checked every 5 min, 30-day history)")
    console.kv("Notify on", _truncate(instructions))
    console.kv("Notify via", "server log (add a webhook later)")
    console.kv("Scored by", f"{engine.kind} | {engine.default_model}")
    if backfill is not None:
        console.kv("Backfill", f"last {_hours_label(backfill)} (~1s per backfilled post)")
    typer.echo("")
    if not typer.confirm("Start watching?", default=True):
        console.warn("Aborted.")
        raise typer.Exit(code=1)

    feed_envelope = FeedEnvelope(
        name=feed_name,
        kind="curated",
        poll_interval_seconds=300,
        data={"retention_days": 30},
        sources=[_source_input(s, backfill) for s in subreddits],
    )
    feed_body = feed_envelope.model_dump(mode="json")

    # Silent dry-run catches a name collision / config-shape issue
    # BEFORE the real create, so the operator sees one clean error
    # rather than a half-applied state.
    ac.api.feed.create(feed_body, dry_run=True)
    feed = ac.api.feed.create(feed_body, dry_run=False)
    if not feed.id:
        console.error("Couldn't set up the watch (server didn't return an id). Try again, or report this.")
        raise typer.Exit(code=1)
    console.success(f"Watching {sources}")

    # Pin the engine's default model so the listener-policy availability
    # check fires at every save (and now): an empty model string would
    # skip the check and let an "Ollama down" config save silently.
    listener_envelope = ListenerEnvelope(
        name=listener_name,
        instructions=instructions,
        kind="semantic",
        delivery_mode="digest",
        data={
            "feed_id": feed.id,
            "engine": {"kind": engine.kind, "model": engine.default_model},
            "notifiers": [
                {"kind": "log", "prefix": f"[{listener_name}]", "include_fields": []},
            ],
            "hit_threshold": 0.7,
            "digest_interval_seconds": 300,
        },
    )
    listener_body = listener_envelope.model_dump(mode="json")

    # The watch exists at this point; a listener failure leaves an
    # orphan, so we surface the cleanup command explicitly. The decorator
    # would still print the API error, but the operator wouldn't know
    # about the half-applied state; name it here.
    try:
        ac.api.listener.create(listener_body, dry_run=True)
    except ApiError as exc:
        _print_api_error(exc)
        console.error(
            f"The watch on {sources} was already set up. "
            f"Tear it down with `magpie feed delete {feed.id}` if you want to retry."
        )
        raise typer.Exit(code=1) from None
    listener = ac.api.listener.create(listener_body, dry_run=False)
    if not listener.id:
        console.error(
            f"Couldn't finish wiring up notifications. The watch on {sources} "
            f"exists; tear it down with `magpie feed delete {feed.id}` if you want to retry."
        )
        raise typer.Exit(code=1)
    console.success(f"You'll be notified when posts match: {_truncate(instructions)}")

    typer.echo("")
    console.header("Here's what a notification will look like:")
    sample = ac.api.listener.payload_sample(listener.id)
    _print_payload_sample(sample)

    typer.echo("")
    console.log("Polls every 5 min.")
    typer.echo("")
    console.header(f"`make dev-tick` fires one pass now; `{_loop_command()}` keeps it ticking.")
    typer.echo("")
    console.log(f"Want a webhook instead of (or in addition to) the server log? `magpie listener edit {listener.id}`.")


def _require_available_engine(ac: AppContext) -> EngineStatus:
    """Probe registered engines and return the first available one.

    On no engines: server misconfiguration, abort with a server-side
    pointer. On every-engine-unreachable: surface each engine's
    `unreachable_reason` so the operator sees what to fix (start
    Ollama, fix OLLAMA_URL, etc.) rather than just a generic message.
    """
    engines = ac.api.engine.list()
    if not engines:
        console.error(
            "The server has no scoring engine registered. "
            "Check `OLLAMA_URL` and `OLLAMA_DEFAULT_MODEL` in the server's env, then restart."
        )
        raise typer.Exit(code=1)

    for engine in engines:
        if engine.available:
            model_label = engine.default_model or "(server default)"
            console.log(f"Scoring with: {engine.kind} | {model_label}")
            return engine

    console.error("No scoring engine is reachable. Posts can't be ranked without one:")
    for engine in engines:
        if engine.unreachable_reason:
            console.error(f"  {engine.kind}: {engine.unreachable_reason}")
        else:
            console.error(f"  {engine.kind}: unavailable")
        if engine.how_to_fix:
            console.error(f"    -> {engine.how_to_fix}")
    raise typer.Exit(code=1)


def _prompt_subreddits() -> list[str]:
    """Parse the comma-separated subreddit prompt, stripping `r/` and
    `/r/` prefixes operators paste in from the URL bar. Re-prompt on an
    empty result rather than crash mid-create."""
    while True:
        raw = typer.prompt(
            "Which subreddits should I watch? (comma-separated)",
            default="ClaudeAI, AI_Agents",
        )
        cleaned: list[str] = []
        for chunk in raw.split(","):
            name = chunk.strip().lstrip("/").removeprefix("r/").strip()
            if name:
                cleaned.append(name)
        if cleaned:
            return cleaned
        console.warn("Need at least one subreddit.")


def _prompt_instructions() -> str:
    """The scoring model reads this verbatim, so empty is a hard
    error; without it there's nothing to filter posts against."""
    while True:
        text = typer.prompt("What should I notify you about? (a sentence or two, plain English)").strip()
        if text:
            return text
        console.warn("Need at least a sentence; without it there's nothing to filter posts against.")


_BACKFILL_MAX_HOURS = 168  # 7 days; Reddit's listing endpoints cap the response anyway
_BACKFILL_DEFAULT_HOURS = 24


def _prompt_backfill() -> timedelta | None:
    """Optional historical window so the first poll has posts to score.

    One prompt, integer hours: `0` means live-only (server policy
    fills `last_event_at = now` and the first poll fetches only
    items posted after that), `1..168` translates to a past
    `last_event_at = now - hours` on every source. Default is 24h
    because the demo is the point of quickstart: out of the box
    you want real posts to score, not an empty feed.

    The prompt names the cost-side of the trade-off: each backfilled
    post is one LLM judge call (a few seconds on local Ollama), so a
    week-long window on a busy subreddit can mean minutes of scoring
    on the first tick. Operator picks; we don't gate it."""
    while True:
        hours = typer.prompt(
            f"How many hours of recent posts to backfill? (0-{_BACKFILL_MAX_HOURS}, 0 = live-only; ~1s per backfilled post)",
            default=_BACKFILL_DEFAULT_HOURS,
            type=int,
        )
        if hours == 0:
            return None
        if 1 <= hours <= _BACKFILL_MAX_HOURS:
            return timedelta(hours=hours)
        console.warn(f"Pick 0 (live-only) or 1-{_BACKFILL_MAX_HOURS}.")


def _hours_label(delta: timedelta) -> str:
    """`72 hours` -> `3 days`; smaller numbers stay in hours so a 24h
    default reads naturally."""
    hours = int(delta.total_seconds() // 3600)
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _source_input(subreddit: str, backfill: timedelta | None) -> SourceInput:
    """Build one starter `SourceInput` for the quickstart feed. When
    `backfill` is set, pin `last_event_at` to `now - backfill` so the
    first poll fetches items from that window forward; otherwise leave
    it None and the server's policy defaults it to `now` (live-only)."""
    return SourceInput(
        spec=RedditSubredditSourceSpec(subreddit=subreddit),
        last_event_at=(datetime.now(UTC) - backfill) if backfill is not None else None,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    out = _SLUG_RE.sub("-", s.lower()).strip("-")
    return out or "quickstart"


def _loop_command() -> str:
    """Shell command that keeps `make dev-tick` ticking at the poll cadence.

    Prefer `watch` when it's on PATH (clearer output for a long-running
    loop). Fall back to a portable `while`/`sleep` loop otherwise so the
    suggestion still works on a fresh macOS (no `watch` by default) or
    any other host without the GNU coreutils watch binary.
    """
    if shutil.which("watch"):
        return "watch -n 300 make dev-tick"
    return "while true; do make dev-tick; sleep 300; done"


def _truncate(text: str, *, limit: int = 80) -> str:
    """Single-line preview of a multi-line prose field (the instructions).
    Whitespace-collapsed and ellipsized so the preview row stays scannable
    even when the operator pasted a paragraph."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
