# Denormalize Feed.account_id onto FeedItem so every read query can
# scope by account at the DB layer (no more "feed_id implies account_id"
# trust). The (account_id, feed_id, id) index replaces (feed_id, id) so
# the existing hot queries (judgment scan, prune, list_recent_items) get
# a tighter range scan with account leading.

from django.db import migrations, models


def _backfill_account_id(apps, schema_editor):
    """Populate FeedItem.account_id from Feed.account_id. Correlated UPDATE
    works on both SQLite and PostgreSQL; runs in one statement."""
    schema_editor.execute(
        """
        UPDATE feeds_feeditem
        SET account_id = (
            SELECT account_id FROM feeds_feed WHERE feeds_feed.id = feeds_feeditem.feed_id
        )
        WHERE account_id = ''
        """
    )


def _noop_reverse(apps, schema_editor):
    # No reverse data action: the column itself is removed on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("feeds", "0003_rename_stream_key_to_stream_label"),
    ]

    operations = [
        # Add the column with default='' so the schema migration succeeds on
        # existing rows; the data migration immediately backfills real values.
        migrations.AddField(
            model_name="feeditem",
            name="account_id",
            field=models.CharField(default="", max_length=26, verbose_name="account id"),
            preserve_default=False,
        ),
        migrations.RunPython(_backfill_account_id, reverse_code=_noop_reverse),
        # Swap the index: (feed_id, id) -> (account_id, feed_id, id).
        migrations.RemoveIndex(
            model_name="feeditem",
            name="feeditem_feed_id_id_idx",
        ),
        migrations.AddIndex(
            model_name="feeditem",
            index=models.Index(
                fields=["account_id", "feed_id", "id"],
                name="feeditem_acct_feed_id_idx",
            ),
        ),
    ]
