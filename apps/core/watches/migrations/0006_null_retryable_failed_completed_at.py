"""One-shot data cleanup for the completed_at-means-terminal rule.

Before this rule, the instant path stamped `completed_at` even on a
retry-pending FAILED (attempts under the cap). Those rows would mis-bucket
as `evaluated[failed]` until they next churn. Null them so the invariant
(`completed_at` set <=> terminal) holds for existing rows too — day-one
summary accuracy without waiting for the queue to re-claim them.

Idempotent + safe: it only touches FAILED rows under the attempts cap (the
retryable ones) ; exhausted FAILED and clean terminals keep their stamp.
The queue itself ignores `completed_at` (claim_due doesn't filter on it),
so this changes nothing about what runs — only what the summary reports.
"""

from django.conf import settings
from django.db import migrations


def _null_retryable_failed(apps, schema_editor):
    run = apps.get_model("watches", "WatchActionRun")
    run.objects.filter(
        state="failed",
        attempts__lt=settings.WATCH_RUN_MAX_ATTEMPTS,
    ).update(completed_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("watches", "0005_watchactionrun_watchrun_activity_idx"),
    ]

    # Reverse can't restore the old timestamps (we never recorded them) and
    # doesn't need to — a re-claim re-stamps on the next terminal transition.
    operations = [
        migrations.RunPython(_null_retryable_failed, migrations.RunPython.noop),
    ]
