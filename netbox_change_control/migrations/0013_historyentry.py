"""
Record the history of each change request.

Merges and reverts are copied from netbox-branching's branch events, which are deleted with the
branch; the earlier status changes were never recorded. A completed request whose branch was
reverted last goes back to Draft, as a revert now does.
"""

from collections import defaultdict

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def record_past_merges_and_reverts(apps, schema_editor):
    BranchEvent = apps.get_model('netbox_branching', 'BranchEvent')
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')
    HistoryEntry = apps.get_model('netbox_change_control', 'HistoryEntry')

    events = (
        BranchEvent.objects.filter(type__in=('merged', 'reverted'), branch__change_request__isnull=False)
        .order_by('time', 'pk')
        .values('branch__change_request', 'type', 'user_id', 'time')
    )
    last_action = defaultdict(str)
    entries = []
    for event in events:
        change_request_id = event['branch__change_request']
        entries.append(
            HistoryEntry(
                change_request_id=change_request_id,
                action=event['type'],
                user_id=event['user_id'],
                time=event['time'],
            )
        )
        last_action[change_request_id] = event['type']
    HistoryEntry.objects.bulk_create(entries)

    reverted = [pk for pk, action in last_action.items() if action == 'reverted']
    ChangeRequest.objects.filter(pk__in=reverted, status='completed').update(status='draft')


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_change_control', '0012_repair_custom_action_names'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoryEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('action', models.CharField(editable=False, max_length=30)),
                ('time', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('change_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='netbox_change_control.changerequest')),
                ('reviewers', models.ManyToManyField(blank=True, editable=False, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'history entry',
                'verbose_name_plural': 'history entries',
                'ordering': ('time', 'pk'),
                'default_permissions': (),
            },
        ),
        migrations.RunPython(record_past_merges_and_reverts, migrations.RunPython.noop),
    ]
