"""
Store each change window to the second, as the edit form shows it.

A window written through the REST API could carry microseconds, which the form drops, so the
next save of the form would read as a move of the window and make the reviews stale.
"""

from django.db import migrations
from django.db.models.functions import TruncSecond

WINDOW_FIELDS = ('scheduled_start', 'scheduled_end')


def window_to_the_second(apps, schema_editor):
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')
    for name in WINDOW_FIELDS:
        ChangeRequest.objects.exclude(**{name: None}).update(**{name: TruncSecond(name)})


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0016_backfill_merged_changes'),
    ]

    operations = [
        migrations.RunPython(window_to_the_second, migrations.RunPython.noop),
    ]
