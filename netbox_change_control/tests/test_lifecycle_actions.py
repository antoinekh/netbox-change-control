"""
Abandoning and reopening a change request.

`status` is a cached view of the policy evaluation, so it is not an editable field. It used to
be, on the bulk edit form and over the REST API, and Completed is terminal: the merge gate
refuses a completed request and nothing reopens one, so setting it by hand blocked a branch
from merging for good with no way back through the interface.

These are the two transitions a person legitimately makes, each behind its own permission.
"""

from django.test import TestCase
from rest_framework import status as http
from users.models import Group, User
from utilities.testing import APITestCase

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequest, Policy, PolicyRule
from netbox_change_control.tests.base import approve, grant, make_branch


class AbandonAndReopenViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.actor = User.objects.create(username='actor')

    def setUp(self):
        self.cr = ChangeRequest.objects.create(
            branch=make_branch('lifeview', self._testMethodName), title='T', requester=self.requester
        )
        self.client.force_login(self.actor)

    def test_abandoning_without_the_permission_is_refused(self):
        grant(self.actor, ['view', 'change'], ChangeRequest)
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/abandon/')
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_reopening_needs_its_own_permission(self):
        self.cr.abandon()
        grant(self.actor, ['view', 'abandon'], ChangeRequest)
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/reopen/')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)


class StatusIsNotWritableTest(APITestCase):
    def setUp(self):
        super().setUp()
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=make_branch('apilife', 'x'), title='T', requester=self.requester)
        grant(self.user, ['view', 'change'], ChangeRequest)

    def test_patching_status_to_completed_is_ignored(self):
        """
        The bug this file exists for. Completed is terminal and the gate refuses it, so a
        writable status was one request away from blocking a branch permanently.
        """
        response = self.client.patch(
            f'/api/plugins/change-control/change-requests/{self.cr.pk}/',
            {'status': ChangeRequestStatusChoices.COMPLETED},
            format='json',
            **self.header,
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_the_abandon_action_needs_its_permission(self):
        response = self.client.post(f'/api/plugins/change-control/change-requests/{self.cr.pk}/abandon/', **self.header)
        self.assertEqual(response.status_code, http.HTTP_403_FORBIDDEN)

    def test_abandoning_a_completed_request_reports_a_conflict(self):
        self.cr.status = ChangeRequestStatusChoices.COMPLETED
        self.cr.save(update_fields=['status'])
        grant(self.user, ['view', 'abandon'], ChangeRequest, name='cr-abandon')
        response = self.client.post(f'/api/plugins/change-control/change-requests/{self.cr.pk}/abandon/', **self.header)
        self.assertEqual(response.status_code, http.HTTP_409_CONFLICT)


class SubmitForReviewPermissionTest(TestCase):
    """
    Submitting is an edit to the request, so it needs the permission to change one.

    It used to need nothing: any signed-in user could push somebody else's draft into review,
    attaching its policies and announcing change_request_submitted to every event rule.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.outsider = User.objects.create(username='outsider')
        cls.group = Group.objects.create(name='Engineers')
        cls.policy = Policy.objects.create(name='One review')
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        self.cr = ChangeRequest.objects.create(
            branch=make_branch('submitperm', self._testMethodName), title='T', requester=self.requester
        )
        self.url = f'/plugins/change-control/change-requests/{self.cr.pk}/submit/'

    def test_a_user_with_only_view_cannot_submit(self):
        grant(self.outsider, ['view'], ChangeRequest, name='view-only')
        self.client.force_login(self.outsider)
        self.client.post(self.url)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.DRAFT)
        self.assertEqual(self.cr.policies.count(), 0)


class ReadOnlyTokenTest(APITestCase):
    """
    The abandon and reopen actions carry their own permission class, which drops NetBox's
    blanket `add_<model>` requirement for POST. Everything else it inherits has to keep
    working, and a read-only token being refused is the part worth pinning: dropping an entry
    from a perms_map is exactly the kind of change that quietly widens more than intended.
    """

    def setUp(self):
        super().setUp()
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=make_branch('rotoken', 'x'), title='T', requester=self.requester)
        grant(self.user, ['view', 'abandon'], ChangeRequest, name='cr-abandon')
        self.url = f'/api/plugins/change-control/change-requests/{self.cr.pk}/abandon/'

    def test_a_write_token_holding_the_permission_may_abandon(self):
        response = self.client.post(self.url, **self.header)
        self.assertEqual(response.status_code, http.HTTP_200_OK)

    def test_a_read_only_token_may_not(self):
        self.token.write_enabled = False
        self.token.save()

        response = self.client.post(self.url, **self.header)

        self.assertEqual(response.status_code, http.HTTP_403_FORBIDDEN)
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)


class DraftIsTheAuthorsTest(TestCase):
    """
    Draft is a state a person holds, not one the evaluation computes.

    Without that, **Return to draft** would be a button that does nothing: the next review,
    policy edit or branch change would push the request straight back into review. It also
    means submitting has to say so explicitly, and has to do its own announcing.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.policy = Policy.objects.create(name='One review')
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        self.cr = ChangeRequest.objects.create(
            branch=make_branch('draft', self._testMethodName), title='T', requester=self.requester
        )

    def test_submitting_attaches_the_policies_and_asks_for_review(self):
        self.assertTrue(self.cr.submit())

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertEqual(self.cr.policies.count(), 1)

    def test_resubmitting_recovers_the_standing_approval(self):
        """
        The reviews are kept, so an author who pulls a change back and changes nothing gets
        the same answer when they submit it again.
        """
        self.cr.submit()
        approve(self.cr, self.reviewer)
        self.cr.return_to_draft()

        self.cr.submit()

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
