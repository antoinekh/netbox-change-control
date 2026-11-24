"""
Abandon every open change request whose branch is already gone.

Deleting a branch now abandons its open request. A branch deleted before that left its request
open with nothing to merge, so the same step is recorded for those, with no user.
"""

from django.db import migrations

OPEN = ('draft', 'needs-review', 'approved', 'rejected')


def abandon_requests_without_a_branch(apps, schema_editor):
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')
    HistoryEntry = apps.get_model('netbox_change_control', 'HistoryEntry')

    orphans = list(ChangeRequest.objects.filter(branch__isnull=True, status__in=OPEN).values_list('pk', flat=True))
    ChangeRequest.objects.filter(pk__in=orphans).update(status='abandoned')
    HistoryEntry.objects.bulk_create(HistoryEntry(change_request_id=pk, action='abandoned') for pk in orphans)


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0013_historyentry'),
    ]

    operations = [
        migrations.RunPython(abandon_requests_without_a_branch, migrations.RunPython.noop),
    ]
