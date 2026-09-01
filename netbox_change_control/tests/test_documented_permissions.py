"""
The permissions page must list every permission, and no permission that does not exist.

It used to say "view_changerequest and friends", which meant a reader had to guess the rest
and an administrator building a group had nothing to work from. Guessing is also how the page
drifts: a permission added later is simply never written down.

This compares the page against the model definitions, so neither can move without the other.
"""

import re
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    ChangeRequestPolicy,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)

DOCS = Path(__file__).resolve().parent.parent.parent / 'docs'
MODELS = (ChangeComment, ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy, PolicyRule, Review)


def declared_permissions():
    """
    Every permission Django and the plugin define for this plugin's models, read from the
    database rather than from a list somebody has to maintain.
    """
    from django.contrib.auth.models import Permission

    return {
        f'netbox_change_control.{p.codename}'
        for p in Permission.objects.filter(content_type__in=[ContentType.objects.get_for_model(m) for m in MODELS])
    }


def documented_permissions(page):
    return set(re.findall(r'`(netbox_change_control\.[a-z_]+)`', (DOCS / page).read_text()))


class PermissionsPageTest(TestCase):
    page = 'permissions.md'

    def test_every_permission_is_documented(self):
        missing = sorted(declared_permissions() - documented_permissions(self.page))
        self.assertEqual(
            missing,
            [],
            f'{self.page} does not mention these permissions, which the plugin defines',
        )

    def test_no_documented_permission_is_invented(self):
        invented = sorted(documented_permissions(self.page) - declared_permissions())
        self.assertEqual(
            invented,
            [],
            f'{self.page} names permissions which do not exist',
        )

    def test_each_one_is_in_a_table_row_with_its_action_and_meaning(self):
        """
        Listing a name in prose is not documenting it. Each has to be a row carrying the
        action to enter on the permission form and what it grants.
        """
        text = (DOCS / self.page).read_text()
        rows = {
            m.group(1)
            for m in re.finditer(r'^\| `(netbox_change_control\.[a-z_]+)` \| `[a-z_]+` \| .+ \|$', text, re.M)
        }
        missing = sorted(declared_permissions() - rows)
        self.assertEqual(missing, [], f'{self.page} mentions these but not as a full table row')


class AdminGuideTest(TestCase):
    """
    The guide carries a role matrix. It does not have to name every permission, but every name
    it does carry has to be real.
    """

    def test_no_invented_permission_in_the_admin_guide(self):
        invented = sorted(documented_permissions('admin-guide.md') - declared_permissions())
        self.assertEqual(invented, [], 'admin-guide.md names permissions which do not exist')


class PolicyBindingPermissionsTest(TestCase):
    """
    The through table binding a policy to a change request defines no permissions.

    Django would create four, and all four were dead: nothing reads them, and an administrator
    granting one to detach a policy by hand gets the binding back at the next re-match.
    """

    def test_the_binding_table_defines_no_permissions(self):
        from django.contrib.auth.models import Permission

        content_type = ContentType.objects.get_for_model(ChangeRequestPolicy)
        self.assertEqual(list(Permission.objects.filter(content_type=content_type)), [])
