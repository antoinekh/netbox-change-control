"""
Tests for per-change comment threads.
"""

from core.choices import ObjectChangeActionChoices
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from netbox_branching.models import ChangeDiff
from users.models import ObjectPermission, User

from netbox_change_control.models import ChangeComment, ChangeRequest, Policy
from netbox_change_control.tests.base import ChangeControlTestCase, approve, make_branch


class ChangeCommentTest(TestCase):
    """
    Comment threads, and the counting of them.

    Resolution is a property of a thread, so every count must agree: the tab badge, the
    banner on the page, and the threads-resolved check.
    """

    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create(username='author')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.policy = Policy.objects.create(name='P')

    def setUp(self):
        self.branch = make_branch('cmt', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.author)
        self.diff = self._diff('object-a')
        self.other_diff = self._diff('object-b')

    def _diff(self, repr_):
        return ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr=repr_,
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            last_updated=None,
        )

    def _comment(self, text='hello', parent=None, diff=None, author=None):
        comment = ChangeComment(
            change_request=self.cr,
            change_diff=diff or self.diff,
            parent=parent,
            author=author or self.reviewer,
            text=text,
        )
        comment.full_clean()
        comment.save()
        return comment

    def _open_thread_count(self):
        return self.cr.change_comments.filter(parent__isnull=True, resolved=False).count()

    def test_a_comment_attaches_to_one_change(self):
        comment = self._comment()
        self.assertEqual(comment.change_diff, self.diff)
        self.assertTrue(comment.is_thread_root)

    def test_a_reply_belongs_to_its_parent_thread(self):
        root = self._comment('concern')
        reply = self._comment('answer', parent=root, author=self.author)
        self.assertEqual(reply.parent, root)
        self.assertFalse(reply.is_thread_root)
        self.assertIn(reply, root.replies.all())

    def test_nesting_is_flattened_to_one_level(self):
        """
        A reply to a reply joins the same thread, which keeps a discussion readable.
        """
        root = self._comment('concern')
        reply = self._comment('answer', parent=root, author=self.author)
        nested = self._comment('follow up', parent=reply)
        self.assertEqual(nested.parent, root)

    def test_a_reply_cannot_cross_to_another_change(self):
        root = self._comment('concern')
        bad = ChangeComment(
            change_request=self.cr,
            change_diff=self.other_diff,
            parent=root,
            author=self.author,
            text='wrong place',
        )
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_threads_start_unresolved(self):
        comment = self._comment()
        self.assertFalse(comment.resolved)

    def test_resolving_a_thread(self):
        comment = self._comment()
        comment.resolved = True
        comment.save(update_fields=['resolved'])
        comment.refresh_from_db()
        self.assertTrue(comment.resolved)
        self.assertEqual(self._open_thread_count(), 0)

    def test_a_reply_does_not_add_to_the_open_thread_count(self):
        root = self._comment('concern')
        self._comment('answer', parent=root, author=self.author)
        self.assertEqual(self._open_thread_count(), 1)

    def test_a_reply_on_a_resolved_thread_counts_nothing(self):
        """
        The case that made the tab badge read 2 while the page said 1.
        """
        root = self._comment('concern')
        self._comment('answer', parent=root, author=self.author)
        root.resolved = True
        root.save(update_fields=['resolved'])

        self.assertEqual(self._open_thread_count(), 0)
        self.assertEqual(self.cr.change_comments.count(), 2)

    def test_resolved_is_forced_off_on_a_reply(self):
        root = self._comment('concern')
        reply = ChangeComment(
            change_request=self.cr,
            change_diff=self.diff,
            parent=root,
            author=self.author,
            text='answer',
            resolved=True,
        )
        reply.save()
        reply.refresh_from_db()
        self.assertFalse(reply.resolved)

    def test_the_badge_and_the_check_agree(self):
        from netbox_change_control.checks import check_threads_resolved
        from netbox_change_control.choices import MergeCheckStatusChoices

        root = self._comment('concern')
        self._comment('answer', parent=root, author=self.author)

        self.assertEqual(self._open_thread_count(), 1)
        self.assertEqual(check_threads_resolved(self.cr).status, MergeCheckStatusChoices.FAILURE)

        root.resolved = True
        root.save(update_fields=['resolved'])

        self.assertEqual(self._open_thread_count(), 0)
        self.assertEqual(check_threads_resolved(self.cr).status, MergeCheckStatusChoices.SUCCESS)


class DeletingARequestWithCommentsTest(ChangeControlTestCase):
    """
    Deleting a change request must not be defeated by its own cascade.

    The cascade fires post_delete on every comment, and the receiver re-ran the checks, which
    re-created the MergeCheck rows against a change request row that was about to disappear.
    The delete then failed on commit with a foreign key violation, so the delete button
    returned a server error on any request that carried a thread.
    """

    branch_prefix = 'del'

    def _diff(self):
        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.models import Policy

        return ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='object-a',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            last_updated=None,
        )

    def _comment(self):
        return ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=self._diff(),
            author=self.reviewer,
            text='Please confirm.',
        )

    def test_a_request_with_an_open_thread_can_be_deleted(self):
        from netbox_change_control.models import MergeCheck

        self._comment()
        self.assertTrue(self.cr.checks.exists())
        pk = self.cr.pk

        self.cr.delete()

        self.assertFalse(ChangeRequest.objects.filter(pk=pk).exists())
        self.assertFalse(MergeCheck.objects.filter(change_request_id=pk).exists())
        self.assertFalse(ChangeComment.objects.filter(change_request_id=pk).exists())

    def test_a_queryset_delete_works_too(self):
        """
        The bulk delete view and the management commands take this path, not Model.delete().
        """
        from netbox_change_control.models import MergeCheck

        self._comment()
        self._approve()
        pk = self.cr.pk

        ChangeRequest.objects.filter(pk=pk).delete()

        self.assertFalse(ChangeRequest.objects.filter(pk=pk).exists())
        self.assertFalse(MergeCheck.objects.filter(change_request_id=pk).exists())

    def test_an_unrelated_request_still_refreshes_during_a_delete(self):
        """
        The guard is per request. Deleting one must not silence the other.
        """
        from netbox_change_control.choices import ChangeRequestStatusChoices
        from netbox_change_control.models import ChangeRequestPolicy

        other_branch = make_branch('del-other', self._testMethodName)
        other = ChangeRequest.objects.create(branch=other_branch, title='Other', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=other, policy=self.policy)
        other.submit()

        self._comment()
        self.cr.delete()

        approve(other, self.reviewer)
        other.refresh_from_db()
        self.assertEqual(other.status, ChangeRequestStatusChoices.APPROVED)


class CommentEditingTest(TestCase):
    """
    A comment can be corrected.

    Until now it could not: ChangeComment had no edit or delete view at all, so a typo in a
    review comment was permanent unless somebody went to the REST API or the Django admin.

    Editing is restricted to the author, for the same reason a review is: a comment is a
    statement attributed to a person, and rewriting somebody else's puts words in their mouth
    in the record a reviewer reads before approving. Deleting follows NetBox's plain model.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.author = User.objects.create(username='author')
        cls.other = User.objects.create(username='other')

    def setUp(self):
        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.models import Policy

        self.branch = make_branch('editcomment', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        policy = Policy.objects.create(name=f'P-{self._testMethodName}'[:100])
        self.diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=policy.pk,
            object_repr='thing',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        self.comment = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.author, text='orignal typo'
        )
        self.url = f'/plugins/change-control/change-comments/{self.comment.pk}/edit/'

    def grant(self, user, actions):
        from django.contrib.contenttypes.models import ContentType

        permission = ObjectPermission.objects.create(name=f'c-{user.pk}-{actions[0]}', actions=list(actions))
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(user)

    def grant_tab_access(self, user):
        """
        The Changes tab is gated on seeing the request and the branch diff, neither of which
        is what these tests are about.
        """
        from django.contrib.contenttypes.models import ContentType

        permission = ObjectPermission.objects.create(name=f'tab-{user.pk}', actions=['view'])
        permission.object_types.set(
            [
                ContentType.objects.get_for_model(ChangeRequest),
                ContentType.objects.get_for_model(ChangeDiff),
            ]
        )
        permission.users.add(user)

    def changes_tab(self, user):
        self.grant_tab_access(user)
        self.client.force_login(user)
        response = self.client.get(f'{self.cr.get_absolute_url()}changes/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_author_can_correct_their_own_comment(self):
        self.grant(self.author, ['view', 'change'])
        self.client.force_login(self.author)

        self.client.post(self.url, {'text': 'original, corrected'})

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.text, 'original, corrected')

    def test_somebody_else_cannot_rewrite_it(self):
        self.grant(self.other, ['view', 'change'])
        self.client.force_login(self.other)

        self.client.post(self.url, {'text': 'words I never wrote'})

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.text, 'orignal typo')

    def test_a_superuser_can(self):
        admin = User.objects.create(username='admin-editor', is_superuser=True)
        self.client.force_login(admin)

        self.client.post(self.url, {'text': 'moderated'})

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.text, 'moderated')

    def test_the_form_cannot_move_or_reattribute_the_comment(self):
        """
        Only the text is editable, so a POST naming another author or another change is
        ignored rather than obeyed.
        """
        self.grant(self.author, ['view', 'change'])
        self.client.force_login(self.author)

        self.client.post(
            self.url,
            {'text': 'still mine', 'author': self.other.pk, 'change_request': self.cr.pk, 'change_diff': ''},
        )

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.author, self.author)
        self.assertEqual(self.comment.change_diff_id, self.diff.pk)

    def test_deleting_needs_the_delete_permission(self):
        self.grant(self.other, ['view', 'change'])
        self.client.force_login(self.other)

        self.client.post(f'/plugins/change-control/change-comments/{self.comment.pk}/delete/', {'confirm': True})

        self.assertTrue(ChangeComment.objects.filter(pk=self.comment.pk).exists())

    def test_deleting_follows_the_plain_netbox_model(self):
        self.grant(self.other, ['view', 'delete'])
        self.client.force_login(self.other)

        self.client.post(f'/plugins/change-control/change-comments/{self.comment.pk}/delete/', {'confirm': True})

        self.assertFalse(ChangeComment.objects.filter(pk=self.comment.pk).exists())

    def test_the_changes_tab_offers_the_author_an_edit_link(self):
        self.grant(self.author, ['view', 'change'])

        self.assertIn(f'/change-comments/{self.comment.pk}/edit/', self.changes_tab(self.author))

    def test_the_changes_tab_offers_no_edit_link_to_anybody_else(self):
        self.grant(self.other, ['view', 'change'])

        self.assertNotIn(f'/change-comments/{self.comment.pk}/edit/', self.changes_tab(self.other))
