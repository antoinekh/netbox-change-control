"""
Clear the cached list columns that a request's status or missing branch no longer allows.

A request abandoned, or left without its branch, by an earlier release kept the cached columns
of its last state, so the list could still show it as ready to merge.
"""

from django.db import migrations


def clear_stale_cached_columns(apps, schema_editor):
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')
    ChangeRequest.objects.filter(cached_gates_cleared=True).exclude(status='approved').update(
        cached_gates_cleared=False
    )
    ChangeRequest.objects.filter(cached_conflicted=True, branch__isnull=True).update(cached_conflicted=False)


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0017_window_to_the_second'),
    ]

    operations = [
        migrations.RunPython(clear_stale_cached_columns, migrations.RunPython.noop),
    ]
