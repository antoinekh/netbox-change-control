"""
Merges and reverts of real, provisioned branches, with protect_main enabled.

The other tests send the branching signals by hand. These run netbox-branching itself, so they
catch what only a real replay does: each change is written to main inside a request whose user
is the author of the change.
"""

import uuid

from django.conf import settings
from django.test import RequestFactory, TransactionTestCase, override_settings
from netbox.context import current_request
from netbox.context_managers import event_tracking
from netbox_branching.choices import BranchMergeStrategyChoices, BranchStatusChoices
from netbox_branching.models import Branch
from netbox_branching.utilities import activate_branch
from users.models import Group, User
from utilities.exceptions import AbortRequest, AbortTransaction

from netbox_change_control.choices import ChangeRequestStatusChoices, HistoryActionChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest
from netbox_change_control.tests.base import approve, make_policy

PROTECT_MAIN = {
    **settings.PLUGINS_CONFIG,
    'netbox_change_control': {
        **settings.PLUGINS_CONFIG.get('netbox_change_control', {}),
        'protect_main': True,
        'protect_main_scope': [],
    },
}


@override_settings(PLUGINS_CONFIG=PROTECT_MAIN)
class EndToEndTest(TransactionTestCase):
    def setUp(self):
        from dcim.models import Site

        group = Group.objects.create(name='Reviewers')
        self.reviewer = User.objects.create(username='reviewer')
        self.reviewer.groups.add(group)
        self.author = User.objects.create(username='author')
        self.merger = User.objects.create(username='merger')
        self.reverter = User.objects.create(username='reverter')
        make_policy(groups=[group], checks=())

        self.site = Site.objects.create(name='Site', slug='site', description='Original')

        self.branch = Branch(name='end-to-end')
        self.branch.save(provision=False)
        self.branch.provision(user=self.author)
        self.branch.refresh_from_db()

        with activate_branch(self.branch), event_tracking(self._request(self.author)):
            site = Site.objects.get(pk=self.site.pk)
            site.snapshot()
            site.description = 'Changed in the branch'
            site.save()

        self.cr = ChangeRequest.objects.create(branch=self.branch, title='End to end', requester=self.author)
        self.cr.submit()
        approve(self.cr, self.reviewer)
        self.cr.refresh_from_db()

    def tearDown(self):
        # Deleting a branch through the ORM drops its schema, which the flush does not.
        for branch in Branch.objects.all():
            branch.delete()

    def _request(self, user):
        request = RequestFactory().get('/')
        request.id = uuid.uuid4()
        request.user = user
        return request

    def _description_on_main(self):
        self.site.refresh_from_db()
        return self.site.description

    def _assert_main_is_protected(self):
        token = current_request.set(self._request(self.author))
        try:
            with self.assertRaises(AbortRequest):
                self.site.description = 'Direct edit'
                self.site.save()
        finally:
            current_request.reset(token)

    def _actions(self):
        return [entry.action for entry in self.cr.history.all()]

    def _approve_again(self):
        self.cr.submit()
        review = self.cr.reviews.get(reviewer=self.reviewer)
        review.decision = ReviewDecisionChoices.APPROVE
        review.save(refresh_snapshot=True)
        self.cr.refresh_from_db()

    def test_the_change_of_an_author_without_bypass_is_merged(self):
        self.branch.merge(user=self.merger, commit=True)

        self.assertEqual(self._description_on_main(), 'Changed in the branch')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)
        self.assertEqual(self.cr.history.get(action=HistoryActionChoices.MERGED).user, self.merger)
        self._assert_main_is_protected()

    def test_a_squash_merge_and_its_revert(self):
        Branch.objects.filter(pk=self.branch.pk).update(merge_strategy=BranchMergeStrategyChoices.SQUASH)
        self.branch.refresh_from_db()

        self.branch.merge(user=self.merger, commit=True)
        self.assertEqual(self._description_on_main(), 'Changed in the branch')

        self.branch.refresh_from_db()
        self.branch.revert(user=self.reverter, commit=True)
        self.assertEqual(self._description_on_main(), 'Original')
        self._assert_main_is_protected()

    def test_a_dry_run_merge_changes_nothing(self):
        with self.assertRaises(AbortTransaction):
            self.branch.merge(user=self.merger, commit=False)

        self.assertEqual(self._description_on_main(), 'Original')
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, BranchStatusChoices.READY)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.assertNotIn(HistoryActionChoices.MERGED, self._actions())
        self._assert_main_is_protected()

    def test_a_dry_run_revert_changes_nothing(self):
        self.branch.merge(user=self.merger, commit=True)
        self.branch.refresh_from_db()

        with self.assertRaises(AbortTransaction):
            self.branch.revert(user=self.reverter, commit=False)

        self.assertEqual(self._description_on_main(), 'Changed in the branch')
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.status, BranchStatusChoices.MERGED)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)
        self.assertNotIn(HistoryActionChoices.REVERTED, self._actions())
        self._assert_main_is_protected()

    def test_a_branch_merged_and_reverted_twice(self):
        self.branch.merge(user=self.merger, commit=True)
        self.branch.refresh_from_db()
        self.branch.revert(user=self.reverter, commit=True)
        self.branch.refresh_from_db()

        self.assertEqual(self._description_on_main(), 'Original')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.DRAFT)
        # can_merge is cached on the instance, so ask a fresh one.
        self.assertFalse(Branch.objects.get(pk=self.branch.pk).can_merge)
        self._assert_main_is_protected()

        self._approve_again()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.branch.merge(user=self.merger, commit=True)
        self.branch.refresh_from_db()
        self.assertEqual(self._description_on_main(), 'Changed in the branch')

        self.branch.revert(user=self.reverter, commit=True)
        self.assertEqual(self._description_on_main(), 'Original')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.DRAFT)
        self._assert_main_is_protected()
        self.assertEqual(
            self._actions(),
            [
                HistoryActionChoices.SUBMITTED,
                HistoryActionChoices.APPROVED,
                HistoryActionChoices.MERGED,
                HistoryActionChoices.REVERTED,
                HistoryActionChoices.SUBMITTED,
                HistoryActionChoices.APPROVED,
                HistoryActionChoices.MERGED,
                HistoryActionChoices.REVERTED,
            ],
        )
