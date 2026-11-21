"""
The history of a change request: each status change, merge and revert, with its user and the
reviews that decided it.
"""

import uuid

from django.test import RequestFactory
from django.urls import reverse
from netbox.context import current_request
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert
from users.models import User

from netbox_change_control.choices import HistoryActionChoices, ReviewDecisionChoices
from netbox_change_control.models import Review
from netbox_change_control.tests.base import ChangeControlTestCase, submit


class HistoryTestCase(ChangeControlTestCase):
    branch_prefix = 'history'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.merger = User.objects.create(username='merger')
        cls.reverter = User.objects.create(username='reverter')

    def _actions(self):
        return [entry.action for entry in self.cr.history.all()]


class MergeHistoryTest(HistoryTestCase):
    approved = True

    def test_each_step_is_recorded_in_order_with_its_user(self):
        post_merge.send(sender=Branch, branch=self.branch, user=self.merger)
        post_revert.send(sender=Branch, branch=self.branch, user=self.reverter)

        steps = [(entry.action, entry.user) for entry in self.cr.history.all()]
        self.assertEqual(
            steps,
            [
                (HistoryActionChoices.SUBMITTED, None),
                (HistoryActionChoices.APPROVED, None),
                (HistoryActionChoices.MERGED, self.merger),
                (HistoryActionChoices.REVERTED, self.reverter),
            ],
        )

    def test_an_approval_names_the_reviews_that_counted(self):
        entry = self.cr.history.get(action=HistoryActionChoices.APPROVED)

        self.assertEqual(list(entry.reviewers.all()), [self.reviewer])

    def test_the_history_tab_lists_the_steps(self):
        post_merge.send(sender=Branch, branch=self.branch, user=self.merger)
        post_revert.send(sender=Branch, branch=self.branch, user=self.reverter)
        self.client.force_login(User.objects.create(username='root', is_superuser=True))

        url = reverse('plugins:netbox_change_control:changerequest_history', kwargs={'pk': self.cr.pk})
        content = self.client.get(url).content.decode()

        for text in ('Opened', 'Approved', 'Merged', 'Reverted', 'merger', 'reverter'):
            self.assertIn(text, content)


class ReviewHistoryTest(HistoryTestCase):
    def test_a_rejection_names_the_reviewer(self):
        Review.objects.create(
            change_request=self.cr, reviewer=self.reviewer, decision=ReviewDecisionChoices.REJECT, comment='no'
        )

        entry = self.cr.history.get(action=HistoryActionChoices.REJECTED)
        self.assertEqual(list(entry.reviewers.all()), [self.reviewer])

    def test_approvals_going_stale_are_recorded(self):
        review = self._approve()
        review.delete()

        self.assertEqual(
            self._actions(),
            [HistoryActionChoices.SUBMITTED, HistoryActionChoices.APPROVED, HistoryActionChoices.NEEDS_REVIEW],
        )


class ManualHistoryTest(HistoryTestCase):
    submitted = False

    def test_the_user_of_the_request_is_recorded(self):
        request = RequestFactory().post('/')
        request.user = self.requester
        request.id = uuid.uuid4()
        token = current_request.set(request)
        try:
            submit(self.cr)
        finally:
            current_request.reset(token)

        self.assertEqual(self.cr.history.get().user, self.requester)

    def test_the_manual_transitions_are_recorded(self):
        submit(self.cr)
        self.cr.return_to_draft()
        submit(self.cr)
        self.cr.abandon()
        self.cr.reopen()

        self.assertEqual(
            self._actions(),
            [
                HistoryActionChoices.SUBMITTED,
                HistoryActionChoices.RETURNED_TO_DRAFT,
                HistoryActionChoices.SUBMITTED,
                HistoryActionChoices.ABANDONED,
                HistoryActionChoices.REOPENED,
            ],
        )


class HistoryBackfillTest(HistoryTestCase):
    """
    netbox-branching runs the plugin migrations again in each branch it migrates.
    """

    def test_running_the_backfill_again_copies_nothing_twice(self):
        from importlib import import_module

        from django.apps import apps
        from netbox_branching.models import BranchEvent

        BranchEvent.objects.create(branch=self.branch, user=self.merger, type='merged')
        backfill = import_module('netbox_change_control.migrations.0013_historyentry').record_past_merges_and_reverts

        backfill(apps, None)
        backfill(apps, None)

        self.assertEqual(self.cr.history.filter(action=HistoryActionChoices.MERGED).count(), 1)
