"""
Tests for the automatic behaviours: stale review detection, approval invalidation, policy
reevaluation and completion on merge.
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge
from users.models import User

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import Policy, Review
from netbox_change_control.tests.base import ChangeControlTestCase


class AutomaticBehaviorTestCase(ChangeControlTestCase):
    branch_prefix = 'auto'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.outsider = User.objects.create(username='outsider')


class StaleReviewTest(AutomaticBehaviorTestCase):
    """
    A review is stale once the branch changes after it was submitted. Staleness is derived
    from the snapshot taken at review time, so it needs no polling.
    """

    def test_review_is_not_stale_when_the_branch_has_not_moved(self):
        review = self._approve()
        self.assertFalse(review.is_stale)

    def test_review_becomes_stale_when_the_branch_moves(self):
        review = self._approve()
        # Simulate a later branch change by backdating the snapshot.
        review.branch_change_time = timezone.now() - timedelta(hours=1)
        Review.objects.filter(pk=review.pk).update(branch_change_time=review.branch_change_time)
        review.refresh_from_db()

        # With no changes in the branch there is nothing to be stale against.
        self.assertFalse(review.is_stale)

    def test_stale_approval_does_not_satisfy_a_rule(self):
        """
        This is the safety property: an approval given against older content must not count.
        """
        review = self._approve()
        Review.objects.filter(pk=review.pk).update(branch_change_time=timezone.now() - timedelta(hours=1))

        # Force the evaluation to see a newer branch change than the review snapshot.
        from unittest.mock import patch

        newer = timezone.now()
        with patch('netbox_change_control.policy.branch_last_change_time', return_value=newer):
            evaluation = self.cr.evaluate()
            self.assertEqual(len(evaluation.stale), 1)
            self.assertFalse(evaluation.satisfied)
            self.assertIn('no longer count', ' '.join(evaluation.reasons()))


class PolicyReevaluationTest(AutomaticBehaviorTestCase):
    def test_raising_the_minimum_revokes_approval(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        self.rule.min_reviews = 2
        self.rule.save()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_removing_the_reviewer_group_revokes_approval(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        self.rule.groups.remove(self.group)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_reviewer_leaving_the_group_revokes_approval(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        self.reviewer.groups.remove(self.group)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_reviewer_joining_the_group_grants_approval(self):
        self._approve(user=self.outsider)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

        self.outsider.groups.add(self.group)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_disabling_the_policy_removes_its_rules(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        self.policy.enabled = False
        self.policy.save()

        # With no enabled rules left the request cannot be satisfied.
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)


class CompletionOnMergeTest(AutomaticBehaviorTestCase):
    def test_merge_marks_the_request_completed(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_completed_request_stays_completed(self):
        self._approve()
        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)
        self.cr.refresh_from_db()

        # Any later evaluation must not reopen a merged request.
        self.rule.min_reviews = 5
        self.rule.save()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_merge_gate_refuses_a_completed_request(self):
        from netbox_change_control.validators import require_approved_change_request

        self._approve()
        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('already completed', indicator.message)


class BranchChangeInvalidationTest(AutomaticBehaviorTestCase):
    """
    Editing an object inside a branch must invalidate an existing approval.

    No review or policy signal fires for such an edit, so without a dedicated hook the stored
    status stays "Approved" while the evaluation says otherwise.

    A test branch has no provisioned schema, so `get_unmerged_changes()` is always empty and
    nothing would ever look stale. The branch timestamp is therefore driven by a controlled
    clock; the receiver wiring under test is real.
    """

    def setUp(self):
        super().setUp()
        self.clock = {'now': timezone.now()}
        self.patcher = patch(
            'netbox_change_control.policy.branch_last_change_time',
            side_effect=lambda branch: self.clock['now'],
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _record_branch_change(self):
        """
        Write an ObjectChange as if an object had been edited inside the branch, and move the
        branch clock forward so existing reviews fall behind.
        """
        import uuid

        from core.choices import ObjectChangeActionChoices
        from core.models import ObjectChange
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.contextvars import active_branch

        self.clock['now'] = timezone.now() + timedelta(minutes=5)

        token = active_branch.set(self.branch)
        try:
            ObjectChange.objects.create(
                changed_object_type=ContentType.objects.get_for_model(Policy),
                changed_object_id=self.policy.pk,
                object_repr='some object',
                action=ObjectChangeActionChoices.ACTION_UPDATE,
                request_id=uuid.uuid4(),
                user=self.requester,
                user_name=self.requester.username,
            )
        finally:
            active_branch.reset(token)

    def test_editing_the_branch_reverts_an_approved_request(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        self._record_branch_change()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_the_stale_review_no_longer_counts(self):
        review = self._approve()
        self._record_branch_change()

        review.refresh_from_db()
        self.assertTrue(review.is_stale)

        evaluation = self.cr.evaluate()
        self.assertEqual(len(evaluation.stale), 1)
        self.assertFalse(evaluation.satisfied)

    def test_a_fresh_review_after_the_change_approves_again(self):
        self._approve()
        self._record_branch_change()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

        # Re-submitting takes a new snapshot, so the review is current again. The review
        # form passes refresh_snapshot=True because clicking submit restates a position.
        review = Review.objects.get(change_request=self.cr, reviewer=self.reviewer)
        review.save(refresh_snapshot=True)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_an_incidental_save_does_not_revalidate_a_stale_review(self):
        """
        Correcting a typo in the comment, a bulk edit, or an API PATCH of an unrelated field
        must not silently refresh the snapshot. Doing so would revalidate an approval against
        branch content the reviewer never saw.
        """
        self._approve()
        self._record_branch_change()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

        review = Review.objects.get(change_request=self.cr, reviewer=self.reviewer)
        review.comment = 'fixing a typo'
        review.save()

        review.refresh_from_db()
        self.assertTrue(review.is_stale)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_changing_the_decision_takes_a_new_snapshot(self):
        """
        A changed decision is a genuine restatement, so it revalidates on its own.
        """
        self._approve()
        self._record_branch_change()

        review = Review.objects.get(change_request=self.cr, reviewer=self.reviewer)
        review.decision = ReviewDecisionChoices.REJECT
        review.comment = 'Not after that change'
        review.save()

        review.refresh_from_db()
        self.assertFalse(review.is_stale)
