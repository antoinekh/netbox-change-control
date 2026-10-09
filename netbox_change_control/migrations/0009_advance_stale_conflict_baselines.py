"""
Do once, for the diffs flagged before netbox-branching 1.2.2, what 1.2.2 does at each sync.

1.2.2 moves `ChangeDiff.original` to main's state when a sync applies a change from main, but
a later sync skips an object main has not changed since, so an older stale flag would stand
for the life of the branch. A flag is stale when main has not changed the object since the
last sync, and `current` then holds the baseline 1.2.2 would have written. As in 1.2.2, only
update diffs qualify: a branch delete against a main update is a real conflict.
"""

from django.db import migrations
from django.db.models import F


def advance_reconciled_baselines(apps, schema_editor):
    Branch = apps.get_model('netbox_branching', 'Branch')
    ChangeDiff = apps.get_model('netbox_branching', 'ChangeDiff')
    ObjectChange = apps.get_model('core', 'ObjectChange')
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')

    advanced_branches = set()
    for branch in Branch.objects.filter(status='ready'):
        flagged = list(
            ChangeDiff.objects.filter(
                branch=branch,
                action='update',
                conflicts__isnull=False,
                original__isnull=False,
                current__isnull=False,
            )
        )
        if not flagged:
            continue

        # Branches created before netbox-branching 0.5.6 have no last_sync.
        last_sync = branch.last_sync or branch.created
        moved_in_main = set(
            ObjectChange.objects.filter(time__gt=last_sync)
            .exclude(application__branch=branch)
            .values_list('changed_object_type_id', 'changed_object_id')
        )
        reconciled = [d.pk for d in flagged if (d.object_type_id, d.object_id) not in moved_in_main]
        if reconciled:
            ChangeDiff.objects.filter(pk__in=reconciled).update(original=F('current'), conflicts=None)
            advanced_branches.add(branch.pk)

    still_conflicted = ChangeDiff.objects.filter(conflicts__isnull=False).values('branch_id')
    ChangeRequest.objects.filter(branch_id__in=advanced_branches).exclude(branch_id__in=still_conflicted).update(
        cached_conflicted=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0008_alter_changerequest_owner_alter_policy_owner'),
    ]

    operations = [
        migrations.RunPython(advance_reconciled_baselines, migrations.RunPython.noop),
    ]
