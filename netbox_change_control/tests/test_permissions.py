"""
The bypass and override permissions must be grantable.

NetBox resolves a permission name as <app_label>.<action>_<model> using rsplit('_', 1). A
custom permission whose trailing component is not a real model cannot be granted by any
object permission, which leaves the exemption available to superusers only. That failure is
silent, so it is pinned here.
"""

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from users.models import ObjectPermission, User
from utilities.permissions import resolve_permission

from netbox_change_control.models import ChangeRequest, Policy
from netbox_change_control.permissions import BYPASS_PERMISSION, OVERRIDE_WINDOW_PERMISSION


class PermissionNameTest(TestCase):
    def test_the_bypass_permission_resolves_to_a_real_model(self):
        app_label, action, model_name = resolve_permission(BYPASS_PERMISSION)
        self.assertEqual(app_label, 'netbox_change_control')
        self.assertEqual(action, 'bypass')
        self.assertEqual(model_name, Policy._meta.model_name)

    def test_the_window_permission_resolves_to_a_real_model(self):
        app_label, action, model_name = resolve_permission(OVERRIDE_WINDOW_PERMISSION)
        self.assertEqual(app_label, 'netbox_change_control')
        self.assertEqual(action, 'override_window')
        self.assertEqual(model_name, ChangeRequest._meta.model_name)


class PermissionGrantTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='ordinary')

    def _reload(self):
        return User.objects.get(pk=self.user.pk)

    def _grant(self, action, model):
        permission = ObjectPermission.objects.create(name=f'grant-{action}', actions=[action])
        permission.users.add(self.user)
        permission.object_types.set([ContentType.objects.get_for_model(model)])

    def test_granting_the_window_override_confers_the_permission(self):
        self._grant('override_window', ChangeRequest)
        self.assertTrue(self._reload().has_perm(OVERRIDE_WINDOW_PERMISSION))

    def test_granting_bypass_confers_it_and_nothing_else(self):
        """
        The regression: a codename which does not resolve to a real model cannot be granted
        at all, so the grant has to be exercised, not just the name.
        """
        self._grant('bypass', Policy)
        user = self._reload()
        self.assertTrue(user.has_perm(BYPASS_PERMISSION))
        self.assertFalse(user.has_perm(OVERRIDE_WINDOW_PERMISSION))
