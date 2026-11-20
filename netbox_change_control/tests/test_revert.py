"""
A reverted merge: the change request goes back to Draft and its reviews go stale.
"""

from unittest.mock import patch

from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert
from users.models import User

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.events import CHANGE_REQUEST_REVERTED
from netbox_change_control.tests.base import ChangeControlTestCase, submit
from netbox_change_control.validators import require_approved_change_request


class RevertTestCase(ChangeControlTestCase):
    branch_prefix = 'revert'
    approved = True

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.merger = User.objects.create(username='merger')
        cls.reverter = User.objects.create(username='reverter')

    def _merge(self):
        post_merge.send(sender=Branch, branch=self.branch, user=self.merger)
        self.cr.refresh_from_db()

    def _revert(self):
        post_revert.send(sender=Branch, branch=self.branch, user=self.reverter)
        self.cr.refresh_from_db()


class RevertStatusTest(RevertTestCase):
    def test_a_revert_returns_the_request_to_draft(self):
        self._merge()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

        self._revert()

        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.DRAFT)

    def test_the_merge_gate_refuses_a_reverted_request(self):
        self._merge()
        self._revert()

        self.assertFalse(require_approved_change_request(self.branch).permitted)

    def test_the_request_and_the_event_rules_are_told(self):
        from extras.models import Notification

        self._merge()
        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                self._revert()

        emitted = [event['event_type'] for call in flush.call_args_list for event in call.args[0]]
        self.assertIn(CHANGE_REQUEST_REVERTED, emitted)
        self.assertTrue(Notification.objects.filter(user=self.requester, event_type=CHANGE_REQUEST_REVERTED).exists())


class RevertStalenessTest(RevertTestCase):
    def test_a_revert_makes_the_reviews_stale(self):
        self._merge()
        self._revert()

        evaluation = self.cr.evaluate()
        self.assertEqual([r.reviewer for r in evaluation.stale], [self.reviewer])
        self.assertFalse(evaluation.satisfied)

    def test_a_resubmitted_request_needs_new_reviews(self):
        self._merge()
        self._revert()

        submit(self.cr)

        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_a_review_given_after_the_revert_counts(self):
        self._merge()
        self._revert()
        submit(self.cr)

        review = self.cr.reviews.get(reviewer=self.reviewer)
        review.decision = ReviewDecisionChoices.APPROVE
        review.save(refresh_snapshot=True)
        self.cr.refresh_from_db()

        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
