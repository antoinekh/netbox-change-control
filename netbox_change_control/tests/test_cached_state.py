"""
The cached columns on the change request list.

Reading conflicts and readiness live costs about eleven queries per row, which is five hundred
for a default page of fifty. Both are cached and refreshed on the events that change them.

A cache is only worth having if it agrees with the truth, so most of this file compares the
cached answer against the live one across the states a change request passes through.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from users.models import Group, User

from netbox_change_control.checks import BUILTIN_CHECKS, run_checks
from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import approve, make_branch


class CachedStateAgreesWithLiveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.policy = Policy.objects.create(name='One review', checks=list(BUILTIN_CHECKS))
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        self.branch = make_branch('cached', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)
        self.cr.refresh_from_db()

    def assert_cache_agrees(self):
        """
        The cached column and the authoritative answer must say the same thing.

        They are allowed to differ only where the cache deliberately knows less: it does not
        run merge validators registered by other plugins. No other plugin is loaded here.
        """
        self.cr.refresh_from_db()
        self.assertEqual(
            self.cr.cached_ready_to_merge,
            self.cr.is_ready_to_merge,
            'the cached readiness disagrees with a live evaluation',
        )
        self.assertEqual(
            self.cr.cached_conflicted,
            bool(self.cr.conflicts),
            'the cached conflict flag disagrees with a live evaluation',
        )

    def test_a_new_request_agrees(self):
        self.assert_cache_agrees()

    def test_an_approved_request_agrees(self):
        approve(self.cr, self.reviewer)
        self.assert_cache_agrees()

    def test_a_request_with_a_failing_check_agrees(self):
        approve(self.cr, self.reviewer)
        self.cr.refresh_from_db()
        self.cr.checks.filter(name='no-conflicts').update(status=MergeCheckStatusChoices.FAILURE)
        run_checks(self.cr)
        self.assert_cache_agrees()

    def test_a_rejected_request_agrees(self):
        from netbox_change_control.choices import ReviewDecisionChoices
        from netbox_change_control.models import Review

        Review.objects.create(
            change_request=self.cr,
            reviewer=self.reviewer,
            decision=ReviewDecisionChoices.REJECT,
            comment='no',
        )
        self.assert_cache_agrees()

    def test_a_request_whose_branch_is_gone_agrees(self):
        approve(self.cr, self.reviewer)
        self.branch.delete()
        self.assert_cache_agrees()

    def test_removing_an_approval_clears_the_cache(self):
        approve(self.cr, self.reviewer)
        self.cr.refresh_from_db()
        was = self.cr.cached_gates_cleared

        self.cr.reviews.all().delete()
        self.cr.refresh_from_db()

        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.assertFalse(self.cr.cached_gates_cleared)
        self.assert_cache_agrees()
        self.assertIsNotNone(was)


class WindowIsNotCachedTest(TestCase):
    """
    A window opens because the clock moved, and nothing happens to hang a refresh on.

    So the window is deliberately left out of the cached flag and evaluated at read time from
    the two fields the row already carries.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.policy = Policy.objects.create(name='One review')
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def test_a_future_window_is_not_ready_without_any_refresh(self):
        from datetime import timedelta

        from django.utils import timezone

        branch = make_branch('window', 'cached')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        approve(cr, self.reviewer)
        cr.refresh_from_db()
        self.assertTrue(cr.cached_gates_cleared)
        self.assertTrue(cr.cached_ready_to_merge)

        # Move the window into the future by writing the fields only. Nothing refreshes the
        # cache, and the answer still has to change.
        ChangeRequest.objects.filter(pk=cr.pk).update(scheduled_start=timezone.now() + timedelta(hours=2))
        cr.refresh_from_db()

        self.assertTrue(cr.cached_gates_cleared)
        self.assertFalse(cr.cached_ready_to_merge)


class ListCostTest(TestCase):
    """
    The point of the cache: the list must not get more expensive as it gets longer.
    """

    def test_the_query_count_does_not_grow_with_the_number_of_rows(self):
        user = User.objects.create(username='lister', is_superuser=True)
        group = Group.objects.create(name='Engineers')
        reviewer = User.objects.create(username='rev')
        reviewer.groups.add(group)
        policy = Policy.objects.create(name='One review', checks=list(BUILTIN_CHECKS))
        PolicyRule.objects.create(policy=policy, name='One engineer', min_reviews=1).groups.set([group])

        kept = None
        for i in range(10):
            cr = ChangeRequest.objects.create(branch=make_branch('cost', f'{i}'), title=f'CR {i}', requester=user)
            ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
            approve(cr, reviewer)
            kept = kept or cr

        self.client.force_login(user)
        with CaptureQueriesContext(connection) as ten_rows:
            self.assertEqual(self.client.get('/plugins/change-control/change-requests/').status_code, 200)

        ChangeRequest.objects.exclude(pk=kept.pk).delete()
        with CaptureQueriesContext(connection) as one_row:
            self.client.get('/plugins/change-control/change-requests/')

        per_row = (len(ten_rows.captured_queries) - len(one_row.captured_queries)) / 9
        self.assertLessEqual(
            per_row,
            0.5,
            f'the change request list costs ~{per_row:.1f} queries per row; the columns are not reading the cache',
        )
