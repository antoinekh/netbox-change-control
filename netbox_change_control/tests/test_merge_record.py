"""
A merge keeps the record of what it put into main, so the change can still be read once the
branch is deleted.
"""

from importlib import import_module

from django.apps import apps
from django.test import Client
from django.urls import reverse
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert
from users.models import User

from netbox_change_control.choices import ChangeRequestStatusChoices, ChangesSource, HistoryActionChoices
from netbox_change_control.models import ChangeComment, ChangeRequest, HistoryEntry, MergedChange
from netbox_change_control.tests.base import ChangeControlTestCase, make_diff


class MergeRecordTest(ChangeControlTestCase):
    branch_prefix = 'record'
    approved = True

    def setUp(self):
        super().setUp()
        self.diff = make_diff(self.branch, object_id=self.policy.pk, modified={'status': 'planned'})
        self.thread = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.reviewer, text='Why planned?'
        )

    def _merge(self):
        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)
        self.cr.refresh_from_db()

    def _changes_tab(self):
        client = Client()
        client.force_login(User.objects.create(username=f'reader-{User.objects.count()}', is_superuser=True))
        return client.get(reverse('plugins:netbox_change_control:changerequest_changes', args=[self.cr.pk]))

    def test_an_open_request_reads_its_branch(self):
        self.assertEqual(self.cr.changes_source, ChangesSource.BRANCH)

    def test_a_merge_keeps_what_it_put_into_main(self):
        self._merge()

        change = self.cr.last_merge.merged_changes.get()
        self.assertEqual((change.object_id, change.modified), (self.policy.pk, {'status': 'planned'}))
        self.assertEqual(self.cr.changes_source, ChangesSource.MERGE)

    def test_the_record_and_its_threads_outlive_the_branch(self):
        self._merge()

        Branch.objects.get(pk=self.branch.pk).delete()

        self.cr.refresh_from_db()
        self.assertEqual([change.object_repr for change in self.cr.changes()], [self.diff.object_repr])
        page = self._changes_tab().content.decode()
        self.assertIn('Why planned?', page)
        self.assertNotIn('Discussion on objects no longer in the change', page)

    def test_a_branch_deleted_before_any_merge_leaves_only_the_discussion(self):
        Branch.objects.get(pk=self.branch.pk).delete()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.changes_source, ChangesSource.NONE)
        self.assertIn('Discussion on objects no longer in the change', self._changes_tab().content.decode())

    def test_each_merge_keeps_its_own_record(self):
        self._merge()
        post_revert.send(sender=Branch, branch=self.branch, user=self.requester)
        self.cr.refresh_from_db()
        self.cr.submit()
        self.cr.reviews.get(reviewer=self.reviewer).save(refresh_snapshot=True)
        self._merge()

        merges = self.cr.history.filter(action=HistoryActionChoices.MERGED)
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)
        self.assertEqual([merge.merged_changes.count() for merge in merges], [1, 1])
        self.assertEqual(self.cr.last_merge, merges.last())


class MergeRecordBackfillTest(ChangeControlTestCase):
    """
    netbox-branching runs the plugin migrations again in each branch, so the backfill runs twice.
    """

    branch_prefix = 'backfill'

    def test_it_fills_once(self):
        diff = make_diff(self.branch, object_id=self.policy.pk)
        comment = ChangeComment.objects.create(change_request=self.cr, change_diff=diff, author=self.reviewer, text='?')
        ChangeComment.objects.filter(pk=comment.pk).update(object_type=None, object_id=None)
        ChangeRequest.objects.filter(pk=self.cr.pk).update(status=ChangeRequestStatusChoices.COMPLETED)
        HistoryEntry.objects.create(change_request=self.cr, action=HistoryActionChoices.MERGED)
        migration = import_module('netbox_change_control.migrations.0016_backfill_merged_changes')

        for _ in range(2):
            migration.fill_comment_objects(apps, None)
            migration.fill_merged_changes(apps, None)

        comment.refresh_from_db()
        self.assertEqual((comment.object_type_id, comment.object_id), (diff.object_type_id, diff.object_id))
        self.assertEqual(MergedChange.objects.filter(merge__change_request=self.cr).count(), 1)
