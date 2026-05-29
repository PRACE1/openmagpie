"""Recompute every Source.spec_hash with the canonical
sort_keys=True serializer.

`_hash_spec` previously ran sha256 over `spec.model_dump_json()`,
which is field-declaration order in Pydantic v2 (not alphabetical).
Any future SourceSpec field reorder / alias / populate_by_name on a
subclass would silently change every hash and break dedup on
existing rows. The runtime now canonicalizes via
`json.dumps(..., sort_keys=True, separators=(",", ":"))`; this
migration backfills the same canonical hash on every row so the new
set_sources path matches the old set's spec_hash column.
"""

import hashlib
import json

from django.db import migrations


def _canonical_hash(spec: dict) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _recompute(apps, schema_editor) -> None:
    Source = apps.get_model("feeds", "Source")
    rows = list(Source.objects.iterator())
    dirty = []
    for row in rows:
        new_hash = _canonical_hash(row.spec or {})
        if new_hash != row.spec_hash:
            row.spec_hash = new_hash
            dirty.append(row)
    if dirty:
        Source.objects.bulk_update(dirty, ["spec_hash"])


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0012_alter_feeditem_source_kind_and_more"),
    ]
    operations = [
        # Forward-only by design: the prior hash function isn't
        # canonical-safe, so a reverse data migration would re-introduce
        # the bug. Schema-roll-back to 0012 doesn't break anything
        # because spec_hash is opaque to readers.
        migrations.RunPython(_recompute, migrations.RunPython.noop),
    ]
