"""
Collapsing the repeated refresh of one change request.

Refreshing means recomputing the status and running the checks, and doing it on every event
that could change the answer is how the plugin stays consistent. The cost is that one user
action is often many events: submitting a request attaches every matching policy, and each
binding is its own signal, so a request governed by three policies refreshed three times and
ran every check three times for an identical answer.
"""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from users.models import Group, ObjectPermission, User

from netbox_change_control.batching import batched, schedule_refresh
from netbox_change_control.checks import BUILTIN_CHECKS
from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequest, Policy, PolicyRule
from netbox_change_control.tests.base import make_branch


def count_check_runs():
    """
    Count real calls to run_checks, wherever they are reached from.
    """
    import netbox_change_control.checks as checks_mod

    calls = []
    original = checks_mod.run_checks

    def counted(change_request):
        calls.append(change_request.pk)
        return original(change_request)

    return calls, patch.object(checks_mod, 'run_checks', counted)


class SubmitCollapsesTheBurstTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        for i in range(3):
            policy = Policy.objects.create(name=f'Policy {i}', checks=list(BUILTIN_CHECKS))
            PolicyRule.objects.create(policy=policy, name='One engineer', min_reviews=1).groups.set([cls.group])

        permission = ObjectPermission.objects.create(name='cr', actions=['view', 'change'])
        from django.contrib.contenttypes.models import ContentType

        permission.object_types.add(ContentType.objects.get_for_model(ChangeRequest))
        permission.users.add(cls.requester)

    def setUp(self):
        self.branch = make_branch('batch', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        self.client.force_login(self.requester)

    def submit(self):
        return self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/submit/')

    def test_submitting_runs_the_checks_once_for_three_policies(self):
        """
        The measurement this file exists for. Before, three policies meant four runs: one per
        binding signal and one more from the view.
        """
        calls, counting = count_check_runs()
        with counting:
            self.submit()

        self.assertEqual(len(calls), 1, f'checks ran {len(calls)} times for one submission')

    def test_submitting_still_leaves_the_request_correct(self):
        """
        Collapsing the work must not lose any of it.
        """
        self.submit()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.policies.count(), 3)
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertEqual(self.cr.checks.count(), len(BUILTIN_CHECKS))
        self.assertTrue(all(c.completed for c in self.cr.checks.all()))

    def test_the_query_count_does_not_grow_with_the_number_of_policies(self):
        with CaptureQueriesContext(connection) as three:
            self.submit()

        for i in range(3, 8):
            policy = Policy.objects.create(name=f'Extra {i}', checks=list(BUILTIN_CHECKS))
            PolicyRule.objects.create(policy=policy, name='One engineer', min_reviews=1).groups.set([self.group])
        other = ChangeRequest.objects.create(branch=make_branch('batch', 'eight'), title='T2', requester=self.requester)
        with CaptureQueriesContext(connection) as eight:
            self.client.post(f'/plugins/change-control/change-requests/{other.pk}/submit/')

        # Matching five more policies costs a little, but nothing like a further check run
        # each. Before batching, each policy added a full refresh and a full check suite.
        per_policy = (len(eight.captured_queries) - len(three.captured_queries)) / 5
        self.assertLess(
            per_policy,
            6,
            f'each additional policy costs ~{per_policy:.1f} queries on submit',
        )


class BatchedBlockTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.policy = Policy.objects.create(name='P', checks=list(BUILTIN_CHECKS))
        PolicyRule.objects.create(policy=cls.policy, name='r', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        self.cr = ChangeRequest.objects.create(
            branch=make_branch('block', self._testMethodName), title='T', requester=self.requester
        )

    def test_outside_a_block_the_refresh_is_immediate(self):
        """
        Most callers are not part of a burst, and nothing changes for them.
        """
        calls, counting = count_check_runs()
        with counting:
            schedule_refresh(self.cr)

        self.assertEqual(len(calls), 1)

    def test_inside_a_block_repeated_requests_collapse(self):
        calls, counting = count_check_runs()
        with counting, batched():
            for _ in range(5):
                schedule_refresh(self.cr)
            self.assertEqual(len(calls), 0, 'work ran before the block ended')

        self.assertEqual(len(calls), 1)

    def test_two_different_requests_each_get_one_refresh(self):
        other = ChangeRequest.objects.create(branch=make_branch('block', 'other'), title='T2', requester=self.requester)
        calls, counting = count_check_runs()
        with counting, batched():
            schedule_refresh(self.cr)
            schedule_refresh(other)
            schedule_refresh(self.cr)

        self.assertCountEqual(calls, [self.cr.pk, other.pk])

    def test_nesting_is_safe_and_only_the_outermost_flushes(self):
        calls, counting = count_check_runs()
        with counting, batched():
            with batched():
                schedule_refresh(self.cr)
            self.assertEqual(len(calls), 0, 'an inner block flushed')

        self.assertEqual(len(calls), 1)

    def test_a_block_that_raises_flushes_nothing(self):
        """
        The work would be computed against a state that is about to roll back.
        """
        calls, counting = count_check_runs()
        with counting:
            with self.assertRaises(RuntimeError):
                with batched():
                    schedule_refresh(self.cr)
                    raise RuntimeError('boom')

        self.assertEqual(calls, [])

    def test_a_deleted_request_is_skipped_rather_than_raising(self):
        pk = self.cr.pk
        with batched():
            schedule_refresh(self.cr)
            ChangeRequest.objects.filter(pk=pk).delete()
        # Reaching here without an exception is the assertion.
        self.assertFalse(ChangeRequest.objects.filter(pk=pk).exists())
