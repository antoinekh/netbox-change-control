"""
Conflicts with main.

A conflict can appear with no event on the change request at all: somebody edits the same
field in main and branching recomputes the diff. The page must show that, and the stored
check result must not stay green.
"""

from core.choices import ObjectChangeActionChoices
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.models import Branch, ChangeDiff
from users.models import User

from netbox_change_control.checks import _registry, check_no_conflicts, register_builtin_checks, run_checks
from netbox_change_control.choices import MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy
from netbox_change_control.tests.base import make_branch


def main_moved(branch, diff, moved=True):
    """
    Stand in for Branch.get_unsynced_changes, which reports what main changed after the last
    sync. A conflict is real only when main has moved since then.
    """
    from unittest.mock import patch

    rows = [(diff.object_type_id, diff.object_id)] if moved else []

    class _Qs:
        def values_list(self, *args, **kwargs):
            return rows

    return patch.object(type(branch), 'get_unsynced_changes', return_value=_Qs())


class ConflictVisibilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        # The policy is both the object the diffs point at and, once attached, what makes the
        # no-conflicts check apply. A built-in is registered but never applied on its own.
        cls.policy = Policy.objects.create(name='P', checks=['no-conflicts'])

    def setUp(self):
        self._saved = dict(_registry)
        _registry.clear()
        register_builtin_checks(['no-conflicts'])
        self.branch = make_branch('conf', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)
        # Attaching a policy does not run the checks, and these tests need the stored result
        # which the diff receiver later flips.
        run_checks(self.cr)

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved)

    def _diff(self, conflicts=None):
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='conflicted-object',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        # ChangeDiff.save() recomputes conflicts from the states, so set them directly.
        ChangeDiff.objects.filter(pk=diff.pk).update(conflicts=conflicts)
        return ChangeDiff.objects.get(pk=diff.pk)

    def test_a_clean_branch_reports_no_conflicts(self):
        self._diff(conflicts=None)
        self.assertEqual(self.cr.conflicts, [])
        self.assertFalse(self.cr.has_conflicts)

    def test_conflicts_are_read_live(self):
        # ChangeDiff.save() rewrites object_repr from the live object, so read it back rather
        # than asserting on the value this test passed in.
        diff = self._diff(conflicts=['device_type'])
        with main_moved(self.branch, diff):
            self.assertTrue(self.cr.has_conflicts)
            self.assertEqual(
                [(d.object_repr, d.conflicts) for d in self.cr.conflicts],
                [(diff.object_repr, ['device_type'])],
            )

    def test_the_check_reports_a_conflict(self):
        diff = self._diff(conflicts=['device_type'])
        with main_moved(self.branch, diff):
            result = check_no_conflicts(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.FAILURE)
        self.assertIn(diff.object_repr, result.summary)

    def test_a_conflict_appearing_later_flips_the_stored_result(self):
        """
        The bug this pins: the check ran while the branch was clean, main then changed the
        same field, and the stored result stayed green while the page said otherwise.
        """
        clean = self._diff(conflicts=None)
        stored = MergeCheck.objects.get(change_request=self.cr, name='no-conflicts')
        self.assertEqual(stored.status, MergeCheckStatusChoices.SUCCESS)

        # Branching rewrites the diff when main changes the same field.
        ChangeDiff.objects.filter(pk=clean.pk).update(conflicts=['device_type'])
        clean.refresh_from_db()
        with main_moved(self.branch, clean):
            clean.save()

        stored.refresh_from_db()
        self.assertEqual(stored.status, MergeCheckStatusChoices.FAILURE)

    def test_a_conflict_being_resolved_flips_it_back(self):
        conflicted = self._diff(conflicts=['device_type'])
        with main_moved(self.branch, conflicted):
            conflicted.save()
        stored = MergeCheck.objects.get(change_request=self.cr, name='no-conflicts')
        stored.refresh_from_db()
        self.assertEqual(stored.status, MergeCheckStatusChoices.FAILURE)

        ChangeDiff.objects.filter(pk=conflicted.pk).update(conflicts=None)
        conflicted.refresh_from_db()
        conflicted.save()

        stored.refresh_from_db()
        self.assertEqual(stored.status, MergeCheckStatusChoices.SUCCESS)

    def test_a_deleted_branch_reports_no_conflicts(self):
        self._diff(conflicts=['device_type'])
        self.branch.delete()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.conflicts, [])
        self.assertFalse(self.cr.has_conflicts)


class StaleBaselineTest(TestCase):
    """
    Branching never advances a diff's baseline, so a field main touched before the last sync
    stays flagged as conflicting forever, even after a sync brought main's value in and the
    branch is fully up to date.

    In git terms that is a fast-forward. Failing the merge on it teaches reviewers to
    acknowledge conflicts by reflex, which is exactly what must not happen for a real one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.policy = Policy.objects.create(name='P')

    def setUp(self):
        self._saved = dict(_registry)
        _registry.clear()
        register_builtin_checks(['no-conflicts'])
        self.branch = make_branch('stale', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        self.diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='flagged-object',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        ChangeDiff.objects.filter(pk=self.diff.pk).update(conflicts=['device_type'])
        self.diff.refresh_from_db()

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved)

    def _main_moved_since_sync(self, moved):
        """
        Stand in for Branch.get_unsynced_changes, which reports what main changed after the
        last sync.
        """
        from unittest.mock import patch

        rows = [(self.diff.object_type_id, self.diff.object_id)] if moved else []

        class _Qs:
            def values_list(self, *args, **kwargs):
                return rows

        return patch.object(Branch, 'get_unsynced_changes', return_value=_Qs())

    def test_a_flag_with_no_movement_in_main_is_not_a_conflict(self):
        from netbox_change_control.conflicts import conflicting_diffs, stale_baseline_diffs

        with self._main_moved_since_sync(False):
            self.assertEqual(conflicting_diffs(self.branch), [])
            self.assertEqual(len(stale_baseline_diffs(self.branch)), 1)

    def test_a_flag_with_movement_in_main_is_a_real_conflict(self):
        from netbox_change_control.conflicts import conflicting_diffs, stale_baseline_diffs

        with self._main_moved_since_sync(True):
            self.assertEqual(len(conflicting_diffs(self.branch)), 1)
            self.assertEqual(stale_baseline_diffs(self.branch), [])

    def test_the_check_passes_on_a_reconciled_flag(self):
        from netbox_change_control.checks import check_no_conflicts

        with self._main_moved_since_sync(False):
            result = check_no_conflicts(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.SUCCESS)
        self.assertIn('already reconciled', result.summary)

    def test_the_check_fails_on_a_real_conflict(self):
        from netbox_change_control.checks import check_no_conflicts

        with self._main_moved_since_sync(True):
            result = check_no_conflicts(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.FAILURE)

    def test_the_page_shows_a_reconciled_flag_as_a_note_not_a_conflict(self):
        with self._main_moved_since_sync(False):
            self.assertFalse(self.cr.has_conflicts)
            self.assertEqual(len(self.cr.reconciled_conflicts), 1)
