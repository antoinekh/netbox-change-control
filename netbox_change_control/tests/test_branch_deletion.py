"""
A change request is the record of who approved what. It must survive its branch.
"""

from django.test import TestCase
from netbox_branching.models import Branch
from users.models import User

from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest, Policy
from netbox_change_control.tests.base import ChangeControlTestCase


class BranchDeletionTest(ChangeControlTestCase):
    branch_prefix = 'doomed'
    approved = True

    def test_branch_name_is_recorded_on_save(self):
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.branch_name, self.branch.name)

    def test_a_rename_updates_the_recorded_name(self):
        self.branch.name = 'renamed-branch'
        self.branch.save()
        self.cr.save()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.branch_name, 'renamed-branch')

    def test_the_request_survives_branch_deletion(self):
        pk = self.cr.pk
        self.branch.delete()

        cr = ChangeRequest.objects.get(pk=pk)
        self.assertIsNone(cr.branch_id)
        self.assertTrue(cr.branch_deleted)
        self.assertEqual(cr.title, self.cr.title)

    def test_the_branch_name_survives_branch_deletion(self):
        self.branch.delete()
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.branch_name, self.branch.name)
        self.assertEqual(self.cr.branch_label, self.branch.name)

    def test_the_reviews_survive_branch_deletion(self):
        self.branch.delete()
        self.cr.refresh_from_db()
        reviews = list(self.cr.reviews.all())
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reviewer, self.reviewer)
        self.assertEqual(reviews[0].decision, ReviewDecisionChoices.APPROVE)

    def test_the_applied_policies_survive_branch_deletion(self):
        self.branch.delete()
        self.cr.refresh_from_db()
        self.assertEqual([b.policy.name for b in self.cr.policy_bindings.all()], ['One review'])

    def test_evaluation_still_works_without_a_branch(self):
        self.branch.delete()
        self.cr.refresh_from_db()
        evaluation = self.cr.evaluate()
        self.assertTrue(evaluation.satisfied)
        self.assertEqual(evaluation.stale, [])

    def test_checks_are_skipped_without_a_branch(self):
        from netbox_change_control.checks import check_branch_has_changes, check_no_conflicts

        self.branch.delete()
        self.cr.refresh_from_db()
        for check in (check_branch_has_changes, check_no_conflicts):
            self.assertEqual(check(self.cr).status, MergeCheckStatusChoices.SKIPPED)

    def test_comment_threads_survive_branch_deletion(self):
        """
        Deleting a branch removes its ChangeDiff rows. A cascade there would take the whole
        discussion with it, leaving a change request that says it was reviewed but not why.
        """

        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.models import ChangeComment

        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='circuit-42',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        root = ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=diff,
            author=self.reviewer,
            text='Is this bandwidth right?',
        )
        ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=diff,
            parent=root,
            author=self.requester,
            text='Yes, confirmed.',
        )
        # ChangeDiff.save() rewrites object_repr from the live object, so read the label
        # back rather than asserting on the value this test passed in.
        expected_label = diff.object_repr
        self.assertEqual(root.change_label, expected_label)
        self.assertTrue(expected_label)

        self.branch.delete()

        comments = list(ChangeComment.objects.filter(change_request=self.cr).order_by('pk'))
        self.assertEqual(len(comments), 2)
        self.assertIsNone(comments[0].change_diff_id)
        self.assertEqual(comments[0].change_label, expected_label)
        self.assertEqual(comments[0].text, 'Is this bandwidth right?')
        self.assertEqual(comments[1].parent_id, root.pk)

    def test_syncing_policies_without_a_branch_keeps_the_record(self):
        from netbox_change_control.policy import sync_policies

        self.branch.delete()
        self.cr.refresh_from_db()
        bindings = sync_policies(self.cr)
        self.assertEqual([b.policy.name for b in bindings], ['One review'])


class OrphanedRequestPagesTest(TestCase):
    """
    Every page of a change request must still render once its branch is gone.

    The detail view read `instance.branch.can_merge` directly, which raises AttributeError on
    a deleted branch. The guarded model property existed but the view had not been switched
    over, so viewing the record crashed with a server error.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='viewer', is_superuser=True)
        cls.requester = User.objects.create(username='orphan-requester')
        branch = Branch.objects.create(name='to-be-deleted')
        cls.cr = ChangeRequest.objects.create(branch=branch, title='Orphaned', requester=cls.requester)
        branch.delete()
        cls.cr.refresh_from_db()

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_detail_page_renders(self):
        response = self.client.get(self.cr.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('to-be-deleted', response.content.decode())

    def test_the_changes_tab_renders(self):
        from django.urls import reverse

        response = self.client.get(reverse('plugins:netbox_change_control:changerequest_changes', args=[self.cr.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_reviews_tab_renders(self):
        from django.urls import reverse

        response = self.client.get(reverse('plugins:netbox_change_control:changerequest_reviews', args=[self.cr.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_list_renders(self):
        from django.urls import reverse

        response = self.client.get(reverse('plugins:netbox_change_control:changerequest_list'))
        self.assertEqual(response.status_code, 200)

    def test_it_is_never_mergeable(self):
        self.assertIsNone(self.cr.merge_indicator)
        self.assertFalse(self.cr.is_ready_to_merge)
        self.assertIn('deleted', self.cr.merge_blocked_reason)


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
        from netbox_change_control.choices import MergeCheckStatusChoices
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
