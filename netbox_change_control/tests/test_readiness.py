"""
"Approved" and "ready to merge" are different things, and the interface must not conflate
them. Approval is the people gate; checks and the change window are separate gates.
"""

from datetime import timedelta

from django.utils import timezone

from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.tests.base import ChangeControlTestCase, pass_checks


class MergeReadinessTest(ChangeControlTestCase):
    branch_prefix = 'ready'
    approved = True

    def test_approved_and_all_gates_clear_is_ready(self):
        pass_checks(self.cr)
        self.assertTrue(self.cr.is_approved)
        self.assertTrue(self.cr.is_ready_to_merge)
        self.assertEqual(self.cr.merge_blocked_reason, '')

    def test_approved_but_a_failing_check_is_not_ready(self):
        """
        This is the case that read as a contradiction on the page: a big "Approved" badge
        beside an unresolved thread.
        """
        pass_checks(self.cr)
        self.cr.checks.filter(name='threads-resolved').update(status=MergeCheckStatusChoices.FAILURE)

        self.assertTrue(self.cr.is_approved)
        self.assertFalse(self.cr.is_ready_to_merge)
        self.assertIn('Required checks are not passing', self.cr.merge_blocked_reason)

    def test_approved_but_outside_the_window_is_not_ready(self):
        pass_checks(self.cr)
        self.cr.scheduled_start = timezone.now() + timedelta(hours=3)
        self.cr.save()

        self.assertTrue(self.cr.is_approved)
        self.assertFalse(self.cr.is_ready_to_merge)
        self.assertIn('window opens', self.cr.merge_blocked_reason)

    def test_an_unapproved_request_is_not_ready(self):
        self.cr.reviews.all().delete()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertFalse(self.cr.is_ready_to_merge)

    def test_a_deleted_branch_is_never_ready(self):
        self.branch.delete()
        self.cr.refresh_from_db()
        self.assertIsNone(self.cr.merge_indicator)
        self.assertFalse(self.cr.is_ready_to_merge)
        self.assertIn('branch has been deleted', self.cr.merge_blocked_reason)
