"""
Conflicts with main.

A conflict can appear with no event on the change request at all: somebody edits the same
field in main and branching recomputes the diff. The page must show that, and the stored
check result must not stay green.
"""

import uuid
from importlib import import_module

from core.choices import ObjectChangeActionChoices
from core.models import ObjectChange
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from netbox_branching.choices import BranchStatusChoices
from netbox_branching.models import Branch, ChangeDiff
from users.models import User

from netbox_change_control.checks import _registry, check_no_conflicts, register_builtin_checks, run_checks
from netbox_change_control.choices import MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy
from netbox_change_control.tests.base import make_branch


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

    def test_the_conflict_list_is_read_live(self):
        """
        `conflicts` returns the actual objects and is always computed now. It is read once per
        page, on the change request itself, so the query it costs is fine there.
        """
        # ChangeDiff.save() rewrites object_repr from the live object, so read it back rather
        # than asserting on the value this test passed in.
        diff = self._diff(conflicts=['device_type'])
        self.assertEqual(
            [(d.object_repr, d.conflicts) for d in self.cr.conflicts],
            [(diff.object_repr, ['device_type'])],
        )

    def test_the_conflict_flag_is_cached_and_follows_a_refresh(self):
        """
        `has_conflicts` reads a cached column, because the change request list shows it once
        per row and computing it live cost two queries each time.

        It is refreshed by the same events that re-run the checks, which is what the diff
        receiver does when branching recomputes a diff.
        """
        from netbox_change_control.policy import refresh_cached_state

        self._diff(conflicts=['device_type'])
        refresh_cached_state(self.cr)

        self.cr.refresh_from_db()
        self.assertTrue(self.cr.has_conflicts)
        self.assertTrue(self.cr.cached_conflicted)

    def test_the_cached_flag_clears_when_the_conflict_goes(self):
        from netbox_change_control.policy import refresh_cached_state

        diff = self._diff(conflicts=['device_type'])
        refresh_cached_state(self.cr)
        self.cr.refresh_from_db()
        self.assertTrue(self.cr.has_conflicts)

        ChangeDiff.objects.filter(pk=diff.pk).update(conflicts=None)
        refresh_cached_state(self.cr)
        self.cr.refresh_from_db()
        self.assertFalse(self.cr.has_conflicts)

    def test_the_check_reports_a_conflict(self):
        diff = self._diff(conflicts=['device_type'])
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
        clean.save()

        stored.refresh_from_db()
        self.assertEqual(stored.status, MergeCheckStatusChoices.FAILURE)

    def test_a_conflict_being_resolved_flips_it_back(self):
        conflicted = self._diff(conflicts=['device_type'])
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


class AdvanceReconciledBaselinesTest(TestCase):
    """
    A diff flagged before netbox-branching 1.2.2 keeps a stale baseline, because a later sync
    skips an object main has not changed since. The data migration advances it once.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.policy = Policy.objects.create(name='P')
        cls.object_type = ContentType.objects.get_for_model(Policy)

    def setUp(self):
        self.branch = make_branch('baseline', self._testMethodName)
        Branch.objects.filter(pk=self.branch.pk).update(status=BranchStatusChoices.READY, last_sync=timezone.now())
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)

    def _flagged_diff(self, action=ObjectChangeActionChoices.ACTION_UPDATE):
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=self.object_type,
            object_id=self.policy.pk,
            object_repr='flagged-object',
            action=action,
        )
        # The states of the sequence in the documentation: main moved before the last sync,
        # the branch edited the field after it.
        ChangeDiff.objects.filter(pk=diff.pk).update(
            original={'device_type': 6},
            modified={'device_type': 12},
            current={'device_type': 5},
            conflicts=['device_type'],
        )
        ChangeRequest.objects.filter(pk=self.cr.pk).update(cached_conflicted=True)
        return diff

    def _migrate(self):
        from django.apps import apps

        migration = import_module('netbox_change_control.migrations.0009_advance_stale_conflict_baselines')
        migration.advance_reconciled_baselines(apps, None)

    def test_a_flag_with_no_movement_in_main_is_cleared(self):
        diff = self._flagged_diff()
        self._migrate()

        diff.refresh_from_db()
        self.assertEqual(diff.original, {'device_type': 5})
        self.assertIsNone(diff.conflicts)
        self.cr.refresh_from_db()
        self.assertFalse(self.cr.cached_conflicted)

    def test_the_cleared_baseline_is_the_one_branching_would_compute(self):
        """
        Saving the diff makes branching recompute its conflicts from the new baseline.
        """
        diff = self._flagged_diff()
        self._migrate()

        diff.refresh_from_db()
        diff.save()
        self.assertIsNone(diff.conflicts)

    def test_a_flag_with_movement_in_main_is_kept(self):
        diff = self._flagged_diff()
        ObjectChange.objects.create(
            user=self.requester,
            request_id=uuid.uuid4(),
            changed_object_type=self.object_type,
            changed_object_id=self.policy.pk,
            object_repr='flagged-object',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        self._migrate()

        diff.refresh_from_db()
        self.assertEqual(diff.conflicts, ['device_type'])
        self.cr.refresh_from_db()
        self.assertTrue(self.cr.cached_conflicted)

    def test_a_branch_delete_is_kept(self):
        diff = self._flagged_diff(action=ObjectChangeActionChoices.ACTION_DELETE)
        self._migrate()

        diff.refresh_from_db()
        self.assertEqual(diff.conflicts, ['device_type'])
