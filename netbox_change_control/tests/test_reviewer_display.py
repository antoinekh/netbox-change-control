"""
How a rule says who may satisfy it.

The panel used to expand the rule into everybody it currently resolved to. A group of fifteen
therefore printed fifteen usernames, on every rule that group satisfied, on two pages, and
buried the approval counts that are the point of the panel. It also went stale in a way the
policy never does: the list moved as people joined and left while the rule had not changed.

It now shows the rule as written. The one thing the expansion did better, warning that nobody
at all is eligible, is kept.
"""

from django.test import TestCase
from users.models import Group, User

from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import make_branch


class ReviewerSummaryTest(TestCase):
    def rule(self, groups=(), users=()):
        policy = Policy.objects.create(name=f'P-{self._testMethodName}'[:100])
        rule = PolicyRule.objects.create(policy=policy, name='r', min_reviews=1)
        rule.groups.set(groups)
        rule.users.set(users)
        return rule

    def test_a_rule_naming_an_empty_group_reports_nobody(self):
        """
        The trap the expanded list did catch: a rule pointing at a group with no members can
        never be satisfied, and the panel has to say so.
        """
        empty = Group.objects.create(name='Nobody Here')

        summary = self.rule(groups=[empty]).reviewer_summary

        self.assertEqual(summary.group_names, ('Nobody Here',))
        self.assertFalse(summary.anybody)

    def test_a_rule_naming_nothing_at_all_reports_nobody(self):
        summary = self.rule().reviewer_summary

        self.assertFalse(summary.anybody)

    def test_a_named_user_needs_no_extra_query(self):
        """
        The eligibility test is skipped when the rule names somebody directly, because the
        answer is already known.
        """
        dave = User.objects.create(username='dave')
        rule = self.rule(users=[dave])
        rule = PolicyRule.objects.prefetch_related('groups', 'users').get(pk=rule.pk)

        with self.assertNumQueries(0):
            self.assertTrue(rule.reviewer_summary.anybody)


class ReviewerDisplayInThePagesTest(TestCase):
    """
    Both pages render the same shared include, so they cannot drift apart.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Change Engineers')
        # A group large enough that expanding it would be the problem this fixed.
        cls.members = [User.objects.create(username=f'eng{i:02d}') for i in range(12)]
        for member in cls.members:
            member.groups.add(cls.group)

        cls.policy = Policy.objects.create(name='Device changes')
        PolicyRule.objects.create(policy=cls.policy, name='Two engineers', min_reviews=2).groups.set([cls.group])

        cls.admin = User.objects.create(username='admin-viewer', is_superuser=True)

    def setUp(self):
        self.branch = make_branch('display', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)
        self.cr.refresh_from_db()
        self.client.force_login(self.admin)

    def test_an_empty_group_is_named_and_called_out(self):
        """
        A rule pointing at a group with no members can never be satisfied. Naming the group is
        the difference between knowing that and knowing what to fix.
        """
        empty = Group.objects.create(name='Nobody Here')
        PolicyRule.objects.create(policy=self.policy, name='A lead', min_reviews=1).groups.set([empty])

        html = self.client.get(self.cr.get_absolute_url()).content.decode()

        self.assertIn('Nobody Here', html)
        self.assertIn('can never be satisfied', html)

    def test_the_page_cost_does_not_grow_with_the_group(self):
        """
        Naming the group rather than expanding it should also stop the page paying per member.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as small:
            self.client.get(self.cr.get_absolute_url())

        big = Group.objects.create(name='Everyone')
        for i in range(40):
            User.objects.create(username=f'extra{i:02d}').groups.add(big)
        PolicyRule.objects.create(policy=self.policy, name='Anybody', min_reviews=1).groups.set([big])

        with CaptureQueriesContext(connection) as large:
            self.client.get(self.cr.get_absolute_url())

        # One more rule costs a little; forty more people must cost nothing.
        self.assertLessEqual(len(large.captured_queries) - len(small.captured_queries), 6)
