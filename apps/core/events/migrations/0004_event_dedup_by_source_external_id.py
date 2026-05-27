# Swap the Event dedup constraint from (kind, listener_id, feed_item_id)
# to (kind, listener_id, source, external_id). The new key matches the
# logical invariant — "this listener has already been notified about this
# source-content" — so a post re-emitted after FeedItem retention prune
# can't deliver twice as a duplicate.
#
# `source` and `external_id` are added as columns (denormalized from
# FeedItem at persist time); `feed_item_id` stays on the row as
# provenance only, no longer the dedup key.
#
# Greenfield: existing Event rows are wiped rather than backfilled. The
# next pipeline cycle re-creates whatever hits matter.

from django.db import migrations, models


def _reset_events(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    Event.objects.all().delete()


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0003_alter_event_kind"),
    ]

    operations = [
        # 1. Wipe existing events: the new constraint shape can't be
        #    backfilled from feed_item_id alone (the FeedItem may be
        #    pruned), and we don't preserve pre-launch event history.
        migrations.RunPython(_reset_events, reverse_code=_noop_reverse),
        # 2. Add the source-shape columns. Defaults are "" because the
        #    table is now empty; new writes set them explicitly.
        migrations.AddField(
            model_name="event",
            name="source",
            field=models.CharField(default="", max_length=64, verbose_name="source"),
        ),
        migrations.AddField(
            model_name="event",
            name="external_id",
            field=models.CharField(default="", max_length=255, verbose_name="external id"),
        ),
        # 3. Swap the unique constraint.
        migrations.RemoveConstraint(
            model_name="event",
            name="uniq_event_kind_listener_item",
        ),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.UniqueConstraint(
                fields=("kind", "listener_id", "source", "external_id"),
                name="uniq_event_kind_listener_source",
            ),
        ),
    ]
