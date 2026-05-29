"""Rename the FeedItem source-attribute trio.

After the Source-table refactor, the `stream_*` prefix on FeedItem
clashed with the new noun ("source" is the place data comes from,
not "stream"). Drops the lingering noun overload by giving the trio
a consistent `source_*` prefix:

  source         → source_kind   (the connector kind, e.g. "reddit_subreddit")
  stream_label   → source_label  (display label, e.g. "r/ClaudeCowork")
  stream_meta    → source_meta   (tags carried from Source.meta)

Plain Django `RenameField` ops; column data is preserved end-to-end.
The unique constraint that referenced `source` is recreated with the
new column name in the same migration so the index plan stays intact.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0010_source_columnar_refactor"),
    ]
    operations = [
        # Drop the constraint that references `source` so the rename
        # of that column doesn't trip Django's auto-detection.
        migrations.RemoveConstraint(
            model_name="feeditem",
            name="uniq_feeditem_per_feed_source_external",
        ),
        migrations.RenameField(model_name="feeditem", old_name="source", new_name="source_kind"),
        migrations.RenameField(model_name="feeditem", old_name="stream_label", new_name="source_label"),
        migrations.RenameField(model_name="feeditem", old_name="stream_meta", new_name="source_meta"),
        # Recreate the unique constraint on the renamed column.
        migrations.AddConstraint(
            model_name="feeditem",
            constraint=models.UniqueConstraint(
                fields=("feed_id", "source_kind", "external_id"),
                name="uniq_feeditem_per_feed_source_external",
            ),
        ),
    ]
