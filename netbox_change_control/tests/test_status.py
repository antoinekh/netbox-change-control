"""
ChangeRequest.status is a cached view of the policy evaluation. These tests pin that it
stays consistent no matter which path creates, changes or removes a review.
"""

from users.models import User

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequestPolicy
from netbox_change_control.tests.base import ChangeControlTestCase


class StatusRefreshTest(ChangeControlTestCase):
    branch_prefix = 'status'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = User.objects.create(username='other')
        cls.other.groups.add(cls.group)

    def test_detaching_the_last_policy_blocks_again(self):
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        ChangeRequestPolicy.objects.filter(change_request=self.cr).delete()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_terminal_status_is_never_reopened(self):
        self.cr.status = ChangeRequestStatusChoices.COMPLETED
        self.cr.save(update_fields=['status'])
        self._approve()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)
