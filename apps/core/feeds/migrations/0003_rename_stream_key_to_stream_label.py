# Rename FeedItem.stream_key -> stream_label. The column's role changed
# from "canonical JSON of the stream's spec" (filterable join key) to
# "display label of the producing stream" (e.g. "r/foo"). RenameField
# preserves data; any existing JSON-shaped values are stale but get
# pruned out within the feed's retention window.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("feeds", "0002_feeditem_feeditem_feed_id_id_idx"),
    ]

    operations = [
        migrations.RenameField(
            model_name="feeditem",
            old_name="stream_key",
            new_name="stream_label",
        ),
        migrations.AlterField(
            model_name="feeditem",
            name="stream_label",
            field=models.CharField(default="", max_length=255, verbose_name="stream label"),
        ),
    ]
