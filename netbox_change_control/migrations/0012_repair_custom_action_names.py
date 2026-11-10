"""
Give the permissions saved with an old checkbox the action that box was meant to grant.

NetBox 4.7 showed this plugin's custom actions as checkboxes named after their old codenames,
such as `abandon_changerequest`. A ticked box stored that name as the action, which granted
nothing, because the plugin checks the action `abandon`. Each old name is replaced by the action
its box stood for, so the permission now grants what the administrator ticked.
"""

from django.db import migrations

RENAMED = {
    'abandon_changerequest': 'abandon',
    'reopen_changerequest': 'reopen',
    'override_window_changerequest': 'override_window',
    'bypass_policy': 'bypass',
}


def repair_custom_action_names(apps, schema_editor):
    ObjectPermission = apps.get_model('users', 'ObjectPermission')
    for permission in ObjectPermission.objects.filter(actions__overlap=list(RENAMED)):
        actions = []
        for action in permission.actions:
            action = RENAMED.get(action, action)
            if action not in actions:
                actions.append(action)
        permission.actions = actions
        permission.save(update_fields=['actions'])


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0011_alter_changerequest_options_alter_policy_options'),
        ('users', '0016_default_ordering_indexes'),
    ]

    operations = [
        migrations.RunPython(repair_custom_action_names, migrations.RunPython.noop),
    ]
