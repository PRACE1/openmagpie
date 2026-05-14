from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listeners", "0002_alter_listener_poll_interval_seconds"),
    ]

    operations = [
        migrations.RenameField(
            model_name="listener",
            old_name="description",
            new_name="instructions",
        ),
        migrations.AlterField(
            model_name="listener",
            name="instructions",
            field=models.TextField(
                help_text=(
                    "What the engine should match against. Format depends on "
                    "engine kind: natural-language prompt for semantic, "
                    "comma-separated terms for a future keyword engine, etc."
                ),
                verbose_name="instructions",
            ),
        ),
    ]
