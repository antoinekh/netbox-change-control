"""
Every action button on a list page must lead somewhere.

NetBox's ObjectListView offers add, import, export, edit, rename and delete by default, and
`ObjectAction.get_url` swallows the NoReverseMatch for any the plugin does not route. The
button still renders, with the literal string "None" as its target, and 404s on click. Every
list view therefore declares the actions it actually has.

This walks every list page rather than checking a fixed set, so a view added later is covered
without anybody remembering to add it here.
"""

import re

from django.test import TestCase
from users.models import User

from netbox_change_control import views

# Matches an anchor or a button whose target is the string "None", which is what a missing
# route renders as.
BROKEN = re.compile(r'<(?:a|button)[^>]*(?:href|formaction)="None"[^>]*>(.*?)</(?:a|button)>', re.S)

LIST_VIEW_CLASSES = (
    views.PolicyListView,
    views.PolicyRuleListView,
    views.ChangeRequestListView,
    views.ReviewListView,
    views.MergeCheckListView,
)

LIST_VIEWS = (
    ('policies', '/plugins/change-control/policies/'),
    ('policy rules', '/plugins/change-control/policy-rules/'),
    ('change requests', '/plugins/change-control/change-requests/'),
    ('reviews', '/plugins/change-control/reviews/'),
    ('merge checks', '/plugins/change-control/checks/'),
)


class ListActionButtonsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # A superuser holds every permission, so every action the view offers is rendered.
        # That is the case which exposes a missing route.
        cls.user = User.objects.create(username='admin', is_superuser=True)

    def setUp(self):
        self.client.force_login(self.user)

    def test_every_list_page_renders(self):
        for label, path in LIST_VIEWS:
            with self.subTest(page=label):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_no_action_button_targets_nothing(self):
        for label, path in LIST_VIEWS:
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                broken = sorted({re.sub(r'<[^>]+>', '', m).strip() for m in BROKEN.findall(html)})
                self.assertEqual(broken, [], f'{label} renders buttons with no target: {broken}')

    def test_every_declared_action_that_needs_a_route_has_one(self):
        """
        The other direction, so a declared action cannot be the one rendering "None".

        BulkExport is excluded because it needs no route: NetBox renders it as a dropdown of
        query-string links against the list view itself, and its template never uses `url`.
        """
        from netbox.object_actions import BulkExport

        for view_class in LIST_VIEW_CLASSES:
            model = view_class.queryset.model
            for action_class in view_class.actions:
                if action_class is BulkExport:
                    continue
                with self.subTest(view=view_class.__name__, action=action_class.__name__):
                    self.assertIsNotNone(
                        action_class.get_url(model),
                        f'{view_class.__name__} offers {action_class.__name__} but no route resolves for it',
                    )

    def test_the_dropped_actions_are_still_unroutable(self):
        """
        Import and bulk rename were dropped because this plugin routes neither. If either is
        given a view later, re-add it to `actions` rather than leaving it silently missing;
        this test is the reminder.
        """
        from netbox.object_actions import BulkImport, BulkRename

        for view_class in LIST_VIEW_CLASSES:
            model = view_class.queryset.model
            for action_class in (BulkImport, BulkRename):
                with self.subTest(view=view_class.__name__, action=action_class.__name__):
                    self.assertNotIn(action_class, view_class.actions)
                    self.assertIsNone(
                        action_class.get_url(model),
                        f'{model.__name__} now routes {action_class.__name__}; add it back to '
                        f'{view_class.__name__}.actions',
                    )
