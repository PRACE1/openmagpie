"""Add a DB-level unique index on `oauth2_provider_application.name`.

Toolkit's `Application` model doesn't enforce uniqueness on `name`.
Two concurrent `bootstrap_oauth_app` first-runs would both pass the
`get_or_create` SELECT and both INSERT, leaving the `magpie-cli`
application present twice — and `Application.objects.get(name=...)`
would then raise `MultipleObjectsReturned` from any token-mint call.

We can't edit the third-party model's Meta, so we add the constraint
via raw SQL. Django's `get_or_create` retries `get()` on `IntegrityError`,
so the index closes the race without any change to bootstrap_oauth_app.

Depends on oauth2_provider's `0001_initial` because that's the
migration that creates the `oauth2_provider_application` table; later
toolkit migrations don't touch the `name` column.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("oauth2_provider", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX IF NOT EXISTS uniq_oauth2_application_name ON oauth2_provider_application (name);"
            ),
            reverse_sql=("DROP INDEX IF EXISTS uniq_oauth2_application_name;"),
        ),
    ]
