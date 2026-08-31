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
            change_request=self.cr, change_diff=self.diff, author=self.victim, text='original'
        )
        response = self.client.patch(
            f'/api/plugins/change-control/change-comments/{comment.pk}/',
            {'text': 'edited', 'author': self.user.pk},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        comment.refresh_from_db()
        self.assertEqual(comment.author, self.victim)
        self.assertEqual(comment.text, 'edited')


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
