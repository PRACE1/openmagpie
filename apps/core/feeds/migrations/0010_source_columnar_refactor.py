"""Reshape Source for indexable kind + hashed unique key + pointer-style FK.

Destructive in dev: truncates Source rows because the SQLite path
can't ALTER a ForeignKey column into a CharField cleanly while
preserving data (FK column name `feed_id` collides with the new
plain CharField `feed_id`). Watermarks on the lost rows aren't
load-bearing — they re-derive on the next poll cycle.

Operators rebuild the source set via `magpie feed set-sources -f` /
`magpie feed add-source` after the migration; nothing has shipped
externally so the destructive path is acceptable.
"""

from django.db import migrations, models


def _truncate_sources(apps, schema_editor) -> None:
    Source = apps.get_model("feeds", "Source")
    Source.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0009_clear_legacy_streams_jsonb"),
    ]
    operations = [
        # Truncate before the column reshape so the destructive ALTER
        # has no rows to break on.
        migrations.RunPython(_truncate_sources, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="source", name="uniq_feed_spec"),
        migrations.RemoveIndex(model_name="source", name="feeds_sourc_feed_id_321dd7_idx"),
        migrations.RemoveField(model_name="source", name="feed"),
        migrations.RemoveField(model_name="source", name="spec_key"),
        migrations.AddField(
            model_name="source",
            name="account_id",
            field=models.CharField(default="", max_length=26),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="source",
            name="feed_id",
            field=models.CharField(default="", max_length=26),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="source",
            name="kind",
            field=models.CharField(default="", max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="source",
            name="spec_hash",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="source",
            name="spec",
            field=models.JSONField(
                help_text=(
                    "Full SourceSpec dump (includes kind for round-trip via the discriminated union)"
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="source",
            constraint=models.UniqueConstraint(
                fields=("account_id", "feed_id", "spec_hash"),
                name="uniq_account_feed_spec_hash",
            ),
        ),
        migrations.AddIndex(
            model_name="source",
            index=models.Index(fields=["account_id", "feed_id", "id"], name="feeds_sourc_acct_feed_id_idx"),
        ),
        migrations.AddIndex(
            model_name="source",
            index=models.Index(fields=["kind"], name="feeds_sourc_kind_idx"),
        ),
    ]
