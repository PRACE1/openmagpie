"""`magpie listener hits` — paginated review of a listener's hits.

Default output is a compact human table (one line per hit,
pipe-delimited). `--json` dumps the raw envelope for scripting.
`--csv` writes a flat CSV ; common keys (`title`, `url`,
`occurred_at`, `author`) are extracted from the snapshot best-effort
and the full snapshot lands in a `data_json` column for downstream
extraction.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import typer

from ... import console
from ...api.listener import HitWire
from ...context import app_ctx
from .._shared import _handle_api_errors
from . import listener_app

_CSV_FIELDS = (
    "id",
    "score",
    "source",
    "external_id",
    "feed_item_id",
    "delivered_at",
    "created_at",
    "title",
    "url",
    "occurred_at",
    "author",
    "data_json",
)

# Heuristic fallback chain for the snapshot keys we surface as
# top-level CSV / table columns. Connectors all settle on these names
# (RSS / Reddit / future kinds) ; if a connector deviates, downstream
# tooling can still parse `data_json` for the full payload.
_SNAPSHOT_TITLE_KEYS = ("title",)
_SNAPSHOT_URL_KEYS = ("url", "link")
_SNAPSHOT_OCCURRED_KEYS = ("occurred_at", "published_at", "created_utc")
_SNAPSHOT_AUTHOR_KEYS = ("author",)


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = data.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _flatten_for_csv(hit: HitWire) -> dict[str, str]:
    data = hit.data or {}
    return {
        "id": hit.id,
        "score": "" if hit.score is None else f"{hit.score:.4f}",
        "source": hit.source,
        "external_id": hit.external_id,
        "feed_item_id": hit.feed_item_id,
        "delivered_at": hit.delivered_at.isoformat() if hit.delivered_at else "",
        "created_at": hit.created_at.isoformat() if hit.created_at else "",
        "title": _pick(data, _SNAPSHOT_TITLE_KEYS),
        "url": _pick(data, _SNAPSHOT_URL_KEYS),
        "occurred_at": _pick(data, _SNAPSHOT_OCCURRED_KEYS),
        "author": _pick(data, _SNAPSHOT_AUTHOR_KEYS),
        "data_json": json.dumps(data, default=str, sort_keys=True),
    }


@listener_app.command("hits")
@_handle_api_errors
def hits(
    listener_id: str = typer.Argument(..., help="Listener id."),
    after: str | None = typer.Option(
        None,
        "--after",
        help="Pagination cursor: return hits older than this hit id.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="Page size (server caps at 200).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Dump the raw API envelope as JSON."),
    csv_out: bool = typer.Option(False, "--csv", help="Write rows as CSV instead of the human table."),
    out: str | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Write CSV to a file path instead of stdout. Implies --csv.",
    ),
) -> None:
    """List this listener's hits, newest first.

    Each hit row carries the FeedItem snapshot taken at hit time, so
    titles / urls / publish timestamps survive feed retention prune.
    Default rendering shows score + title + url ; pipe `--csv` (or
    `--out path.csv`) for a flat spreadsheet of one page. Re-run with
    `--after <last-id>` to walk the next page.
    """
    if out:
        csv_out = True

    ac = app_ctx()
    page = ac.api.listener.hits(listener_id, after=after, limit=limit)

    if json_out:
        typer.echo(page.model_dump_json(indent=2))
        return

    if csv_out:
        rows = [_flatten_for_csv(h) for h in page.items]
        if out:
            with Path(out).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            console.success(f"Wrote {len(rows)} hit(s) to {out}")
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        if page.next_cursor:
            console.warn(f"More pages available ; re-run with --after {page.next_cursor}")
        return

    # Human table.
    if not page.items:
        console.log("No hits in this page.")
        return

    for h in page.items:
        score = "----" if h.score is None else f"{h.score:.2f}"
        delivered = "*" if h.delivered_at else " "
        title = _pick(h.data or {}, _SNAPSHOT_TITLE_KEYS) or "(no title)"
        url = _pick(h.data or {}, _SNAPSHOT_URL_KEYS)
        # 80-col-ish title clip ; full title is in --json / --csv anyway.
        if len(title) > 80:
            title = title[:77] + "..."
        console.log(f"  {score} | {delivered} | {title} | {url} | {h.id}")

    if page.next_cursor:
        console.log("")
        console.log(f"More: magpie listener hits {listener_id} --after {page.next_cursor}")
