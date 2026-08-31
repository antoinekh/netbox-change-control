"""
One merge per branch, however many times auto-merge is reached.

Becoming mergeable is not a single event. One write can arrive at try_auto_merge by more than
one route, and the status is still Approved at the second arrival because the merge has only
been queued, not run. A duplicate job then merges nothing and fails with "not ready to merge",
which reads as a broken merge on a change that in fact went through.

These tests go through the signals rather than calling try_auto_merge directly, because
calling it directly is exactly what hid the problem.
"""

import uuid

from core.choices import JobStatusChoices
from core.models import Job
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.jobs import MergeBranchJob
from netbox_branching.models import Branch
from users.models import Group, User

from netbox_change_control.automerge import try_auto_merge
from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import approve, make_branch


def queued_merges(branch):
    return MergeBranchJob.get_jobs(branch).filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)


class AutoMergeIsEnqueuedOnceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)

    def test_binding_a_zero_rule_policy_queues_one_merge(self):
        """
        The path that used to queue two: attaching a policy refreshes the status, which runs
        the checks, which try to merge; the caller then runs the checks again.
        """
        policy = Policy.objects.create(name='Automatic')
        PolicyRule.objects.create(policy=policy, name='None needed', min_reviews=0)
        branch = make_branch('once', 'zero')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, auto_merge=True)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)

        cr.submit()

        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.APPROVED)
        self.assertEqual(queued_merges(branch).count(), 1)

    def test_a_second_attempt_while_one_is_queued_does_nothing(self):
        policy = Policy.objects.create(name='One review')
        PolicyRule.objects.create(policy=policy, name='One engineer', min_reviews=1).groups.set([self.group])
        branch = make_branch('once', 'second')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, auto_merge=True)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
        cr.submit()
        approve(cr, self.reviewer)
        cr.refresh_from_db()
        cr.checks.update(status=MergeCheckStatusChoices.SUCCESS)

        self.assertEqual(queued_merges(branch).count(), 1)

        cr.refresh_from_db()
        self.assertFalse(try_auto_merge(cr))
        self.assertEqual(queued_merges(branch).count(), 1)

    def test_a_finished_job_does_not_block_a_later_merge(self):
        """
        The guard must read the queue, not the history. A branch whose earlier merge job
        completed, errored or failed must still be able to queue a new one.
        """
        policy = Policy.objects.create(name='Automatic')
        PolicyRule.objects.create(policy=policy, name='None needed', min_reviews=0)
        branch = make_branch('once', 'finished')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, auto_merge=True)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
        cr.submit()

        job = queued_merges(branch).get()
        job.status = JobStatusChoices.STATUS_ERRORED
        job.save()

        self.assertEqual(queued_merges(branch).count(), 0)
        cr.refresh_from_db()
        self.assertTrue(try_auto_merge(cr))
        self.assertEqual(queued_merges(branch).count(), 1)

    def test_a_queued_merge_for_another_branch_is_not_confused_with_this_one(self):
        other = make_branch('once', 'other')
        Job.objects.create(
            name=MergeBranchJob.name,
            object_type=ContentType.objects.get_for_model(Branch),
            object_id=other.pk,
            status=JobStatusChoices.STATUS_PENDING,
            user=self.requester,
            # core.Job identifies a run by a UUID the queue assigns; nothing creates a Job by
            # hand in normal use, so it has no default.
            job_id=uuid.uuid4(),
        )

        policy = Policy.objects.create(name='Automatic')
        PolicyRule.objects.create(policy=policy, name='None needed', min_reviews=0)
        branch = make_branch('once', 'mine')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, auto_merge=True)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
        cr.submit()

        self.assertEqual(queued_merges(branch).count(), 1)
