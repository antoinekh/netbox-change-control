"""
Give a branch state to every review recorded against an empty branch.

Such a review stored no state and so could never go stale. No state means the branch held
nothing when the review last recorded one, so everything in it now came later, and the review's
creation time is a state that every one of those changes is newer than. Reviews of a deleted
branch keep no state: nothing can be added to it.
"""

from django.db import migrations
from django.db.models import F


def snapshot_reviews_of_empty_branches(apps, schema_editor):
    Review = apps.get_model('netbox_change_control', 'Review')
    Review.objects.filter(branch_change_time__isnull=True, change_request__branch__isnull=False).update(
        branch_change_time=F('created')
    )


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0009_advance_stale_conflict_baselines'),
    ]

    operations = [
        migrations.RunPython(snapshot_reviews_of_empty_branches, migrations.RunPython.noop),
    ]
