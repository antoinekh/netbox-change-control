"""
The custom permission actions can be granted with the checkboxes NetBox 4.7 shows for them.

NetBox 4.7 turns each `Meta.permissions` codename into a checkbox on the permission form, and
a ticked box stores the codename as the action. The plugin declared Django-style codenames such
as `abandon_changerequest`, so a ticked box stored that as the action, which NetBox reads as the
permission `abandon_changerequest_changerequest`, which nothing checks: the box granted nothing.
"""

from importlib import import_module

from core.models import ObjectType
from django.apps import apps
from django.test import TestCase
from users.models import ObjectPermission, User

from netbox_change_control.models import ChangeRequest, Policy
from netbox_change_control.permissions import (
    ABANDON_PERMISSION,
    BYPASS_PERMISSION,
    OVERRIDE_WINDOW_PERMISSION,
    REOPEN_PERMISSION,
)

# The documented action, the model it is granted on, and the permission the plugin checks.
ACTIONS = (
    ('abandon', ChangeRequest, ABANDON_PERMISSION),
    ('reopen', ChangeRequest, REOPEN_PERMISSION),
    ('override_window', ChangeRequest, OVERRIDE_WINDOW_PERMISSION),
    ('bypass', Policy, BYPASS_PERMISSION),
)


class CustomActionCheckboxTest(TestCase):
    def test_each_action_is_offered_under_its_documented_name(self):
        from netbox.registry import registry

        for action, model, _permission in ACTIONS:
            with self.subTest(action=action):
                label = f'{model._meta.app_label}.{model._meta.model_name}'
                self.assertIn(action, {a.name for a in registry['model_actions'][label]})

    def test_ticking_the_box_grants_the_permission_the_plugin_checks(self):
        from users.forms import ObjectPermissionForm

        for action, model, permission in ACTIONS:
            with self.subTest(action=action):
                user = User.objects.create(username=f'ticked-{action}')
                form = ObjectPermissionForm(
                    data={
                        'name': f'Ticked {action}',
                        'enabled': True,
                        'object_types_1': [ObjectType.objects.get_for_model(model).pk],
                        f'action_{action}': True,
                        'users': [user.pk],
                    }
                )
                self.assertTrue(form.is_valid(), form.errors)
                form.save()

                self.assertTrue(User.objects.get(pk=user.pk).has_perm(permission))

    def test_the_migration_repairs_a_permission_saved_with_an_old_checkbox(self):
        migration = import_module('netbox_change_control.migrations.0012_repair_custom_action_names')
        ticked = ObjectPermission.objects.create(
            name='old boxes', actions=['view', 'abandon_changerequest', 'reopen_changerequest']
        )
        unrelated = ObjectPermission.objects.create(name='unrelated', actions=['view', 'merge'])

        migration.repair_custom_action_names(apps, None)

        ticked.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(ticked.actions, ['view', 'abandon', 'reopen'])
        self.assertEqual(unrelated.actions, ['view', 'merge'])
