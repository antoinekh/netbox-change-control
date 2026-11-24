"""
A change request is the record of who approved what. It must survive its branch.
"""

from importlib import import_module

from django.apps import apps
from django.core.exceptions import ValidationError

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    HistoryActionChoices,
    MergeCheckStatusChoices,
)
from netbox_change_control.models import ChangeRequest
from netbox_change_control.tests.base import ChangeControlTestCase


class BranchDeletionAbandonsTest(ChangeControlTestCase):
    """
    An open change request always has a branch: deleting the branch abandons the request.
    """

    branch_prefix = 'abandoned-with'
    approved = True

    def _status(self):
        self.cr.refresh_from_db()
        return self.cr.status

    def test_an_open_request_needs_a_branch(self):
        orphan = ChangeRequest(title='No branch', requester=self.requester)

        with self.assertRaises(ValidationError) as raised:
            orphan.full_clean()
        self.assertIn('branch', raised.exception.message_dict)

    def test_the_migration_abandons_requests_left_open_without_a_branch(self):
        ChangeRequest.objects.filter(pk=self.cr.pk).update(branch=None)
        migration = import_module('netbox_change_control.migrations.0014_abandon_requests_without_a_branch')

        migration.abandon_requests_without_a_branch(apps, None)
        migration.abandon_requests_without_a_branch(apps, None)

        self.assertEqual(self._status(), ChangeRequestStatusChoices.ABANDONED)
        self.assertEqual(self.cr.history.filter(action=HistoryActionChoices.ABANDONED).count(), 1)


class BranchRenameTest(ChangeControlTestCase):
    """
    The stored branch name has to follow the branch.

    It exists so the record stays readable once the branch is gone, but it is also what the
    branch filter and the global search read. A rename that did not reach it made a change
    request unfindable by the name shown on its own page.
    """

    branch_prefix = 'renamed'

    def test_a_rename_reaches_the_change_request(self):
        self.branch.name = 'renamed-in-place'
        self.branch.save()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.branch_name, 'renamed-in-place')

    def test_the_filter_finds_the_new_name(self):
        from netbox_change_control.filtersets import ChangeRequestFilterSet
        from netbox_change_control.models import ChangeRequest

        self.branch.name = 'renamed-in-place'
        self.branch.save()

        found = ChangeRequestFilterSet({'branch': 'renamed-in-place'}, ChangeRequest.objects.all()).qs
        self.assertIn(self.cr, found)

    def test_the_filter_no_longer_finds_the_old_name(self):
        from netbox_change_control.filtersets import ChangeRequestFilterSet
        from netbox_change_control.models import ChangeRequest

        old = self.branch.name
        self.branch.name = 'renamed-in-place'
        self.branch.save()

        found = ChangeRequestFilterSet({'branch': old}, ChangeRequest.objects.all()).qs
        self.assertNotIn(self.cr, found)

    def test_the_name_still_survives_deletion_after_a_rename(self):
        self.branch.name = 'renamed-in-place'
        self.branch.save()
        self.branch.delete()

        self.cr.refresh_from_db()
        self.assertTrue(self.cr.branch_deleted)
        self.assertEqual(self.cr.branch_label, 'renamed-in-place')


class AllChecksSkipWithoutABranchTest(ChangeControlTestCase):
    """
    Every built-in check must skip once the branch is gone, not just most of them.

    The documentation promises a branchless request reports its checks as skipped rather than
    failing. `threads-resolved` did not: it counted the comment threads, which survive the
    branch on purpose, and failed on any that were still open. The record of a change nobody
    can merge any more was then permanently marked as blocked.
    """

    branch_prefix = 'skipall'

    def test_every_builtin_skips(self):
        from netbox_change_control.checks import BUILTIN_CHECKS
        from netbox_change_control.models import ChangeComment

        # An open thread, which is what used to make threads-resolved fail.
        ChangeComment.objects.create(
            change_request=self.cr, author=self.requester, text='unresolved', change_label='something'
        )

        self.branch.delete()
        self.cr.refresh_from_db()

        for name, (_label, func) in BUILTIN_CHECKS.items():
            with self.subTest(check=name):
                self.assertEqual(
                    func(self.cr).status,
                    MergeCheckStatusChoices.SKIPPED,
                    f'{name} does not skip on a request whose branch is gone',
                )
