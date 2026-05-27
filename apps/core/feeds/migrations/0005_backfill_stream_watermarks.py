"""Backfill `data.streams[*].last_event_at` for legacy feeds.

Prior to this release, an unset per-stream watermark was a sentinel
meaning "cold-start on first poll": the first poll cycle filled it with
`now()` and fetched nothing. Feed-config policy now fills the watermark
at save time so the poller can rely on `last_event_at is not None` as
an invariant.

Pre-existing feeds created (or imported) but never polled may still
carry a None watermark on one or more streams. Walk every Feed once
and fill them with the migrate-time wall clock: same behavior the
first poll would have produced anyway, just at migrate time so the
poller's new invariant holds for them too.
"""

from django.db import migrations
from django.utils import timezone


def _fill_none_watermarks(apps, schema_editor):
    Feed = apps.get_model("feeds", "Feed")
    now_iso = timezone.now().isoformat()
    for feed in Feed.objects.iterator(chunk_size=200):
        data = feed.data or {}
        streams = data.get("streams") or []
        changed = False
        for watch in streams:
            if not isinstance(watch, dict):
                continue
            if watch.get("last_event_at") is None:
                watch["last_event_at"] = now_iso
                changed = True
        if changed:
            feed.data = data
            feed.save(update_fields=["data", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("feeds", "0004_feeditem_account_id"),
    ]

    operations = [
        migrations.RunPython(_fill_none_watermarks, reverse_code=migrations.RunPython.noop),
    ]
