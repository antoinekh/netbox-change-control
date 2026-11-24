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

from netbox_change_control.checks import BUILTIN_CHECKS
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import approve, make_branch


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
        cr.submit()
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
            cr.submit()
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


class CacheFollowsTheSignalsTest(TestCase):
    """
    The cache has to be refreshed by the events themselves, not by a caller remembering to.

    Every test here drives a real signal path and then compares the cached answer with the
    live one. Calling `refresh_cached_state` directly would only prove the function works,
    which was never the doubt: the risk in a cache is the event nobody wired up.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)

    def make_request(self, checks=(), name='P'):
        policy = Policy.objects.create(name=f'{name}-{self._testMethodName}'[:100], checks=list(checks))
        PolicyRule.objects.create(policy=policy, name='One engineer', min_reviews=1).groups.set([self.group])
        branch = make_branch('signal', self._testMethodName)
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
        cr.submit()
        cr.refresh_from_db()
        return cr, branch

    def assert_agrees(self, cr):
        cr.refresh_from_db()
        self.assertEqual(cr.cached_ready_to_merge, cr.is_ready_to_merge)
        self.assertEqual(cr.cached_conflicted, bool(cr.conflicts))

    def test_a_conflict_updates_the_cache_without_the_no_conflicts_check(self):
        """
        Only the no-conflicts check creates the row the diff receiver used to consult, so a
        request governed by a policy that does not require it had no path back to the cache.
        """
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        cr, branch = self.make_request(checks=())
        self.assertFalse(cr.checks.filter(name='no-conflicts').exists())

        diff = ChangeDiff.objects.create(
            branch=branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=1,
            object_repr='x',
            action='update',
        )
        ChangeDiff.objects.filter(pk=diff.pk).update(conflicts=['name'])
        diff.refresh_from_db()

        diff.save()

        cr.refresh_from_db()
        self.assertTrue(cr.cached_conflicted)
        self.assert_agrees(cr)
