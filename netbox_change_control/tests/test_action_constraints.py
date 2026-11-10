"""
The action buttons honour object permission constraints, as the REST API does.

A NetBox object permission can carry a constraint, and the administration guide recommends
them, `{"requester": "$user"}` to manage only your own change requests among them. The buttons
on a change request page post to small views of their own rather than to NetBox's generic
views, and those checked the permission for the model only: with that constraint, the API
refused to abandon a colleague's request while the Abandon button did it.

Each test grants one constrained permission and checks the action on an object inside the
constraint and on one outside it.
"""

from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from netbox_branching.models import ChangeDiff
from users.models import ObjectPermission, User

from netbox_change_control.choices import (
    ChangeRequestPriorityChoices,
    ChangeRequestStatusChoices,
    MergeCheckStatusChoices,
)
from netbox_change_control.models import ChangeComment, ChangeRequest, MergeCheck, Policy, Review
from netbox_change_control.tests.base import ChangeControlTestCase, make_branch


def grant(user, model, actions, constraints=None):
    permission = ObjectPermission.objects.create(
        name=f'{model._meta.model_name}-{"-".join(actions)}-{user.pk}', actions=list(actions), constraints=constraints
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


class ActionConstraintTest(ChangeControlTestCase):
    branch_prefix = 'constraint'

    def setUp(self):
        super().setUp()
        self.me = User.objects.create(username='constrained')
        self.me.groups.add(self.group)
        self.client = Client()
        self.client.force_login(self.me)
        grant(self.me, ChangeRequest, ['view'])
        # The fixture request belongs to somebody else; this one is mine.
        self.mine = ChangeRequest.objects.create(
            branch=make_branch('constraint-mine', self._testMethodName), title='Mine', requester=self.me
        )

    def _post(self, name, pk, data=None):
        return self.client.post(reverse(f'plugins:netbox_change_control:{name}', args=[pk]), data or {})

    def _status(self, change_request):
        change_request.refresh_from_db()
        return change_request.status

    def test_abandon_honours_the_constraint(self):
        grant(self.me, ChangeRequest, ['abandon'], {'requester': '$user'})
        self.mine.submit()

        self._post('changerequest_abandon', self.cr.pk)
        self._post('changerequest_abandon', self.mine.pk)

        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertEqual(self._status(self.mine), ChangeRequestStatusChoices.ABANDONED)

    def test_reopen_honours_the_constraint(self):
        grant(self.me, ChangeRequest, ['reopen'], {'requester': '$user'})
        self.cr.abandon()
        self.mine.abandon()

        self._post('changerequest_reopen', self.cr.pk)
        self._post('changerequest_reopen', self.mine.pk)

        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.ABANDONED)
        self.assertEqual(self._status(self.mine), ChangeRequestStatusChoices.DRAFT)

    def test_return_to_draft_honours_the_constraint(self):
        grant(self.me, ChangeRequest, ['change'], {'requester': '$user'})
        self.mine.submit()

        self._post('changerequest_return_to_draft', self.cr.pk)
        self._post('changerequest_return_to_draft', self.mine.pk)

        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertEqual(self._status(self.mine), ChangeRequestStatusChoices.DRAFT)

    def test_submit_for_review_honours_the_constraint(self):
        grant(self.me, ChangeRequest, ['change'], {'requester': '$user'})
        self.cr.return_to_draft()

        self._post('changerequest_submit', self.cr.pk)
        self._post('changerequest_submit', self.mine.pk)

        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.DRAFT)
        self.assertEqual(self._status(self.mine), ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_a_review_honours_the_constraint(self):
        grant(self.me, Review, ['add'], {'change_request__priority': ChangeRequestPriorityChoices.HIGH})
        urgent = ChangeRequest.objects.create(
            branch=make_branch('constraint-urgent', self._testMethodName),
            title='Urgent',
            requester=self.requester,
            priority=ChangeRequestPriorityChoices.HIGH,
        )

        self._post('changerequest_review', self.cr.pk, {'decision': 'approve'})
        self._post('changerequest_review', urgent.pk, {'decision': 'approve'})

        self.assertFalse(Review.objects.filter(change_request=self.cr, reviewer=self.me).exists())
        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertTrue(Review.objects.filter(change_request=urgent, reviewer=self.me).exists())

    def test_a_comment_honours_the_constraint(self):
        grant(self.me, ChangeComment, ['add'], {'change_request__requester': '$user'})
        theirs = self._diff(self.branch)
        mine = self._diff(self.mine.branch)

        self._post('changerequest_add_comment', self.cr.pk, {'change_diff': theirs.pk, 'text': 'Hm.'})
        self._post('changerequest_add_comment', self.mine.pk, {'change_diff': mine.pk, 'text': 'Hm.'})

        self.assertFalse(ChangeComment.objects.filter(change_request=self.cr).exists())
        self.assertTrue(ChangeComment.objects.filter(change_request=self.mine).exists())

    def test_resolving_honours_the_constraint(self):
        grant(self.me, ChangeComment, ['view', 'change'], {'author': '$user'})
        diff = self._diff(self.branch)
        theirs = ChangeComment.objects.create(change_request=self.cr, change_diff=diff, author=self.requester, text='a')
        mine = ChangeComment.objects.create(change_request=self.cr, change_diff=diff, author=self.me, text='b')

        self.client.post(reverse('plugins:netbox_change_control:changecomment_resolve', args=[theirs.pk]))
        self.client.post(reverse('plugins:netbox_change_control:changecomment_resolve', args=[mine.pk]))

        theirs.refresh_from_db()
        mine.refresh_from_db()
        self.assertFalse(theirs.resolved)
        self.assertTrue(mine.resolved)

    def test_rerunning_checks_honours_the_constraint(self):
        grant(self.me, MergeCheck, ['view', 'change'], {'name': 'some-other-check'})
        self.cr.checks.update(status=MergeCheckStatusChoices.PENDING, summary='')

        self._post('changerequest_run_checks', self.cr.pk)

        self.assertFalse(self.cr.checks.exclude(status=MergeCheckStatusChoices.PENDING).exists())

    def test_rerunning_checks_without_a_constraint_still_works(self):
        grant(self.me, MergeCheck, ['view', 'change'])
        self.cr.checks.update(status=MergeCheckStatusChoices.PENDING, summary='')

        self._post('changerequest_run_checks', self.cr.pk)

        self.assertTrue(self.cr.checks.exclude(status=MergeCheckStatusChoices.PENDING).exists())

    def test_a_request_outside_the_view_constraint_is_not_found(self):
        ObjectPermission.objects.filter(users=self.me).delete()
        grant(self.me, ChangeRequest, ['view', 'abandon'], {'requester': '$user'})

        response = self._post('changerequest_abandon', self.cr.pk)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._status(self.cr), ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_the_page_offers_only_the_actions_the_constraint_allows(self):
        grant(self.me, ChangeRequest, ['abandon'], {'requester': '$user'})
        self.mine.submit()

        theirs = self.client.get(self.cr.get_absolute_url()).content.decode()
        mine = self.client.get(self.mine.get_absolute_url()).content.decode()

        self.assertFalse(reverse('plugins:netbox_change_control:changerequest_abandon', args=[self.cr.pk]) in theirs)
        self.assertTrue(reverse('plugins:netbox_change_control:changerequest_abandon', args=[self.mine.pk]) in mine)

    def test_the_merge_permission_is_read_for_this_branch(self):
        from netbox_branching.models import Branch

        grant(self.me, Branch, ['merge'], {'name': 'some-other-branch'})
        self.assertFalse(self.client.get(self.cr.get_absolute_url()).context['can_merge_perm'])

        grant(self.me, Branch, ['merge'], {'name': self.branch.name})
        self.assertTrue(self.client.get(self.cr.get_absolute_url()).context['can_merge_perm'])

    @staticmethod
    def _diff(branch):
        return ChangeDiff.objects.create(
            branch=branch,
            action='update',
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=1,
            object_repr='thing',
            original={'a': 1},
            modified={'a': 2},
            current={'a': 1},
        )
