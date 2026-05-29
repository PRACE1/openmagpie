"""Data migration: drop the legacy `streams` key from every curated
feed's `data` JSONB.

The Source-table refactor moved per-source state (spec, watermark,
meta) onto rows; `data.streams` is unused after the polling refactor.
Cleaning it up here keeps the persisted shape aligned with the new
`CuratedFeedConfig` schema (which no longer declares the field).
"""

from django.db import migrations


def _drop_streams_key(apps, schema_editor) -> None:
    Feed = apps.get_model("feeds", "Feed")
    for feed in Feed.objects.iterator():
        if feed.kind != "curated":
            continue
        data = feed.data or {}
        if "streams" not in data:
            continue
        data.pop("streams", None)
        feed.data = data
        feed.save(update_fields=["data", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0008_feeditem_stream_meta"),
    ]
    operations = [
        # Reverse is a no-op: the JSONB key is reconstructible from
        # Source rows (and the existing 0007 migration would re-derive
        # it on the way back).
        migrations.RunPython(_drop_streams_key, migrations.RunPython.noop),
    ]
