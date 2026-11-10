"""
Who a comment is attributed to, and which change it may refer to.

A comment on the Changes tab is part of the record a reviewer reads before approving. If a
token can post one under somebody else's name, it can fake a colleague's sign-off in that
discussion; if it can file one against another request's diff, the comment is invisible where
it belongs and counted as an open thread where it does not.

The review side of this is pinned in test_api.py. This file pins the comment side.
"""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from netbox_branching.models import ChangeDiff
from rest_framework import status
from users.models import ObjectPermission, User
from utilities.testing import APITestCase

from netbox_change_control.models import ChangeComment, ChangeRequest, Policy
from netbox_change_control.tests.base import make_branch


def make_diff(branch, object_id=1, repr_='thing'):
    return ChangeDiff.objects.create(
        branch=branch,
        action='update',
        object_type=ContentType.objects.get_for_model(Policy),
        object_id=object_id,
        object_repr=repr_,
        original={'a': 1},
        modified={'a': 2},
        current={'a': 1},
    )


class ChangeCommentAttributionTest(APITestCase):
    def setUp(self):
        super().setUp()
        self.victim = User.objects.create(username='victim')
        self.requester = User.objects.create(username='requester')
        self.branch = make_branch('comment-attr', 'x')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        self.diff = make_diff(self.branch)

        permission = ObjectPermission.objects.create(name='comment', actions=['view', 'add', 'change'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(self.user)

    def test_the_author_is_the_caller_not_whoever_is_named(self):
        response = self.client.post(
            '/api/plugins/change-control/change-comments/',
            {
                'change_request': self.cr.pk,
                'change_diff': self.diff.pk,
                'author': self.victim.pk,
                'text': 'Looks fine to me',
            },
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        comment = ChangeComment.objects.get(pk=response.data['id'])
        self.assertEqual(comment.author, self.user)
        self.assertNotEqual(comment.author, self.victim)

    def test_a_comment_posted_without_an_author_is_accepted(self):
        """
        The field is read-only, so a well behaved client omits it entirely and the caller is
        still recorded.
        """
        response = self.client.post(
            '/api/plugins/change-control/change-comments/',
            {'change_request': self.cr.pk, 'change_diff': self.diff.pk, 'text': 'Fine'},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ChangeComment.objects.get(pk=response.data['id']).author, self.user)

    def test_editing_a_comment_does_not_reassign_its_author(self):
        comment = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.user, text='original'
        )
        response = self.client.patch(
            f'/api/plugins/change-control/change-comments/{comment.pk}/',
            {'text': 'edited', 'author': self.victim.pk},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual(comment.author, self.user)
        self.assertEqual(comment.text, 'edited')


class ChangeCommentOwnershipAPITest(APITestCase):
    """
    Only the author may rewrite a comment over the API; anybody holding `change_changecomment`
    may still resolve or reopen a thread, which is what that permission is documented for.

    The edit form narrows to the caller's own comments, but the API did not, so a token could
    rewrite a colleague's "do not merge" into "looks fine", still attributed to that colleague.
    """

    def setUp(self):
        super().setUp()
        self.colleague = User.objects.create(username='colleague')
        requester = User.objects.create(username='requester')
        branch = make_branch('comment-owner', 'x')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=requester)
        diff = make_diff(branch)
        self.theirs = ChangeComment.objects.create(
            change_request=cr, change_diff=diff, author=self.colleague, text='Do not merge.'
        )
        self.mine = ChangeComment.objects.create(change_request=cr, change_diff=diff, author=self.user, text='Mine')

        permission = ObjectPermission.objects.create(name='comment', actions=['view', 'change'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(self.user)

    def _patch(self, comment, data):
        return self.client.patch(
            f'/api/plugins/change-control/change-comments/{comment.pk}/', data, format='json', **self.header
        )

    def test_a_colleague_comment_cannot_be_rewritten(self):
        response = self._patch(self.theirs, {'text': 'Looks fine to me.'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.text, 'Do not merge.')

    def test_a_bulk_rewrite_of_a_colleague_comment_is_refused(self):
        response = self.client.patch(
            '/api/plugins/change-control/change-comments/',
            [{'id': self.mine.pk, 'text': 'Still mine'}, {'id': self.theirs.pk, 'text': 'Looks fine to me.'}],
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.text, 'Do not merge.')

    def test_a_colleague_thread_can_be_resolved(self):
        response = self._patch(self.theirs, {'resolved': True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.theirs.refresh_from_db()
        self.assertTrue(self.theirs.resolved)

    def test_restating_the_same_text_while_resolving_is_accepted(self):
        """
        A client sending the whole object back with only `resolved` changed is resolving.
        """
        response = self._patch(self.theirs, {'resolved': True, 'text': 'Do not merge.'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_an_own_comment_can_be_rewritten(self):
        response = self._patch(self.mine, {'text': 'Edited'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.text, 'Edited')

    def test_a_superuser_can_rewrite_any_comment(self):
        self.user.is_superuser = True
        self.user.save()
        response = self._patch(self.theirs, {'text': 'Moderated.'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ChangeCommentBranchScopeTest(APITestCase):
    """
    A comment must refer to a change in its own request's branch.
    """

    def setUp(self):
        super().setUp()
        self.requester = User.objects.create(username='requester')

        self.branch = make_branch('scope-mine', 'x')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='Mine', requester=self.requester)
        self.diff = make_diff(self.branch, object_id=1, repr_='mine')

        self.other_branch = make_branch('scope-other', 'x')
        self.other_cr = ChangeRequest.objects.create(branch=self.other_branch, title='Theirs', requester=self.requester)
        self.other_diff = make_diff(self.other_branch, object_id=2, repr_='theirs')

        permission = ObjectPermission.objects.create(name='comment', actions=['view', 'add'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(self.user)

    def test_a_comment_on_its_own_branch_is_accepted(self):
        response = self.client.post(
            '/api/plugins/change-control/change-comments/',
            {'change_request': self.cr.pk, 'change_diff': self.diff.pk, 'text': 'Fine'},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_comment_naming_another_requests_change_is_refused(self):
        response = self.client.post(
            '/api/plugins/change-control/change-comments/',
            {'change_request': self.cr.pk, 'change_diff': self.other_diff.pk, 'text': 'Sneaky'},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ChangeComment.objects.count(), 0)

    def test_the_model_refuses_it_too(self):
        comment = ChangeComment(change_request=self.cr, change_diff=self.other_diff, author=self.user, text='Sneaky')
        with self.assertRaises(ValidationError):
            comment.full_clean()


class ChangeCommentThreadDepthTest(APITestCase):
    """
    A reply to a reply joins the same thread, on every path.

    The flattening lived in clean(), which reassigns self.parent. NetBox's
    ValidatedModelSerializer runs full_clean() on a throw-away copy and keeps only the original
    attributes, so the REST path stored a grandchild instead. The Changes tab builds its
    threads from roots alone, so such a comment rendered nowhere at all.
    """

    def setUp(self):
        super().setUp()
        self.requester = User.objects.create(username='requester')
        self.branch = make_branch('depth', 'x')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        self.diff = make_diff(self.branch)
        self.root = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.user, text='root'
        )
        self.reply = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.user, parent=self.root, text='reply'
        )

        permission = ObjectPermission.objects.create(name='comment', actions=['view', 'add'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(self.user)

    def test_a_reply_to_a_reply_joins_the_same_thread_over_the_api(self):
        response = self.client.post(
            '/api/plugins/change-control/change-comments/',
            {
                'change_request': self.cr.pk,
                'change_diff': self.diff.pk,
                'parent': self.reply.pk,
                'text': 'grandchild',
            },
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = ChangeComment.objects.get(pk=response.data['id'])
        self.assertEqual(created.parent_id, self.root.pk)

    def test_a_reply_to_a_reply_joins_the_same_thread_through_the_orm(self):
        created = ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=self.diff,
            author=self.user,
            parent=self.reply,
            text='grandchild',
        )
        self.assertEqual(created.parent_id, self.root.pk)


class ChangeCommentStaleDiffTest(APITestCase):
    def test_a_change_that_no_longer_exists_is_a_validation_error(self):
        """
        Not a server error. A branch deleted while a comment is in flight is the realistic
        way to hold an id that has gone.
        """
        requester = User.objects.create(username='requester')
        branch = make_branch('stale', 'x')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=requester)
        diff = make_diff(branch)
        comment = ChangeComment(change_request=cr, change_diff_id=diff.pk, author=self.user, text='x')
        diff.delete()

        with self.assertRaises(ValidationError):
            comment.full_clean()


class ChangeCommentBranchScopeOnUpdateTest(ChangeCommentBranchScopeTest):
    """
    The branch rule has to hold on update as well as on create.
    """

    def test_patching_a_comment_onto_another_branch_is_refused(self):
        comment = ChangeComment.objects.create(
            change_request=self.cr, change_diff=self.diff, author=self.user, text='mine'
        )
        permission = ObjectPermission.objects.create(name='comment-change', actions=['change'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeComment))
        permission.users.add(self.user)

        response = self.client.patch(
            f'/api/plugins/change-control/change-comments/{comment.pk}/',
            {'change_diff': self.other_diff.pk},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        comment.refresh_from_db()
        self.assertEqual(comment.change_diff_id, self.diff.pk)
