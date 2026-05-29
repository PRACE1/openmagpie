"""Data migration: copy each curated feed's `data.streams` JSONB into
Source rows on the new table.

Leaves `data["streams"]` in place for this migration so polling keeps
working through the transition. The follow-up commit that refactors
polling onto Source rows drops the JSONB key in its own data migration.
"""

import ulid
from django.db import migrations
from pydantic import TypeAdapter

from openmagpie_schema.configs import SourceSpec

_SPEC_ADAPTER = TypeAdapter(SourceSpec)


def _copy_streams_to_sources(apps, schema_editor) -> None:
    Feed = apps.get_model("feeds", "Feed")
    Source = apps.get_model("feeds", "Source")

    for feed in Feed.objects.iterator():
        if feed.kind != "curated":
            continue
        streams = (feed.data or {}).get("streams") or []
        rows = []
        for watch in streams:
            spec_dict = watch.get("spec") or {}
            if not spec_dict:
                continue
            try:
                spec_obj = _SPEC_ADAPTER.validate_python(spec_dict)
            except Exception:
                # A malformed legacy entry shouldn't block the migration;
                # operator can re-set it via the upcoming CLI verbs.
                continue
            rows.append(
                Source(
                    id=str(ulid.ulid()),
                    feed=feed,
                    spec=spec_obj.model_dump(mode="json"),
                    spec_key=spec_obj.model_dump_json(),
                    last_event_at=watch.get("last_event_at"),
                    meta={},
                    field_map={},
                )
            )
        if rows:
            Source.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0006_source"),
    ]
    operations = [
        # Reverse is a no-op: dev DB only, and `0006_source` rolling back
        # drops the table anyway, so there's nothing to undo here.
        migrations.RunPython(_copy_streams_to_sources, migrations.RunPython.noop),
    ]
