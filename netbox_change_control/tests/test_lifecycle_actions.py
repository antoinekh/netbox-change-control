"""
Abandoning and reopening a change request.

`status` is a cached view of the policy evaluation, so it is not an editable field. It used to
be, on the bulk edit form and over the REST API, and Completed is terminal: the merge gate
refuses a completed request and nothing reopens one, so setting it by hand blocked a branch
from merging for good with no way back through the interface.

These are the two transitions a person legitimately makes, each behind its own permission.
"""

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework import status as http
from users.models import Group, ObjectPermission, User
from utilities.testing import APITestCase

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import approve, make_branch


def grant(user, actions, model=ChangeRequest, name='cr'):
    permission = ObjectPermission.objects.create(name=name, actions=actions)
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


class AbandonAndReopenModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.policy = Policy.objects.create(name='One review')
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def make_request(self):
        cr = ChangeRequest.objects.create(
            branch=make_branch('life', self._testMethodName), title='T', requester=self.requester
        )
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        cr.refresh_from_db()
        return cr

    def test_an_open_request_can_be_abandoned(self):
        cr = self.make_request()
        self.assertTrue(cr.abandon())
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_a_completed_request_cannot_be_abandoned(self):
        cr = self.make_request()
        cr.status = ChangeRequestStatusChoices.COMPLETED
        cr.save(update_fields=['status'])
        self.assertFalse(cr.abandon())
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_an_abandoned_request_can_be_reopened(self):
        cr = self.make_request()
        cr.abandon()
        self.assertTrue(cr.reopen())
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

    def test_a_completed_request_cannot_be_reopened(self):
        """
        Completed records a merge that happened. Reopening it would invite a second merge of a
        branch already in main.
        """
        cr = self.make_request()
        cr.status = ChangeRequestStatusChoices.COMPLETED
        cr.save(update_fields=['status'])
        self.assertFalse(cr.reopen())
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_reopening_recomputes_rather_than_restoring(self):
        """
        A request approved before it was abandoned must not come back approved if its reviews
        no longer satisfy the policies.
        """
        cr = self.make_request()
        approve(cr, self.reviewer)
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.APPROVED)

        cr.abandon()
        cr.reviews.all().delete()
        cr.refresh_from_db()

        self.assertTrue(cr.reopen())
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)


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
        grant(self.actor, ['view', 'change'])
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/abandon/')
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_abandoning_with_the_permission_works(self):
        grant(self.actor, ['view', 'abandon'])
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/abandon/')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_reopening_needs_its_own_permission(self):
        self.cr.abandon()
        grant(self.actor, ['view', 'abandon'])
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/reopen/')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_reopening_with_the_permission_works(self):
        self.cr.abandon()
        grant(self.actor, ['view', 'reopen'])
        self.client.post(f'/plugins/change-control/change-requests/{self.cr.pk}/reopen/')
        self.cr.refresh_from_db()
        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)


class StatusIsNotWritableTest(APITestCase):
    def setUp(self):
        super().setUp()
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=make_branch('apilife', 'x'), title='T', requester=self.requester)
        grant(self.user, ['view', 'change'])

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

    def test_status_is_still_reported(self):
        response = self.client.get(f'/api/plugins/change-control/change-requests/{self.cr.pk}/', **self.header)
        self.assertEqual(response.data['status'], self.cr.status)

    def test_the_abandon_action_needs_its_permission(self):
        response = self.client.post(f'/api/plugins/change-control/change-requests/{self.cr.pk}/abandon/', **self.header)
        self.assertEqual(response.status_code, http.HTTP_403_FORBIDDEN)

    def test_the_abandon_action_works_with_it(self):
        grant(self.user, ['view', 'abandon'], name='cr-abandon')
        response = self.client.post(f'/api/plugins/change-control/change-requests/{self.cr.pk}/abandon/', **self.header)
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_abandoning_a_completed_request_reports_a_conflict(self):
        self.cr.status = ChangeRequestStatusChoices.COMPLETED
        self.cr.save(update_fields=['status'])
        grant(self.user, ['view', 'abandon'], name='cr-abandon')
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
        grant(self.outsider, ['view'], name='view-only')
        self.client.force_login(self.outsider)
        self.client.post(self.url)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.DRAFT)
        self.assertEqual(self.cr.policies.count(), 0)

    def test_a_user_with_change_can_submit(self):
        grant(self.requester, ['view', 'change'], name='may-change')
        self.client.force_login(self.requester)
        self.client.post(self.url)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertEqual(self.cr.policies.count(), 1)


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
        grant(self.user, ['view', 'abandon'], name='cr-abandon')
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


class ActionButtonPlacementTest(TestCase):
    """
    Abandon and reopen act on the whole change request, so they belong with Edit and Delete in
    the page's control bar. They were in the footer of the Applied policies card, where they
    read as something to do with the policies.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.admin = User.objects.create(username='admin-viewer', is_superuser=True)

    def setUp(self):
        self.cr = ChangeRequest.objects.create(
            branch=make_branch('placement', self._testMethodName), title='T', requester=self.requester
        )
        self.client.force_login(self.admin)

    def controls(self):
        """
        The buttons in the page's control bar, which is where object actions live.
        """
        import re

        html = self.client.get(self.cr.get_absolute_url()).content.decode()
        bar = re.search(
            r'<div class="btn-list justify-content-end mb-2">(.*?)<div class="d-flex justify-content-end">',
            html,
            re.S,
        )
        self.assertIsNotNone(bar, 'the control bar was not found; the NetBox template may have changed')
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', bar.group(1)))

    def policies_card(self):
        import re

        html = self.client.get(self.cr.get_absolute_url()).content.decode()
        card = re.search(r'Applied policies(.*?)Reviews', html, re.S)
        return card.group(1) if card else ''

    def test_abandon_is_in_the_control_bar(self):
        self.assertIn('Abandon', self.controls())

    def test_abandon_is_not_in_the_policies_card(self):
        self.assertNotIn('Abandon', self.policies_card())

    def test_reopen_replaces_it_once_abandoned(self):
        self.cr.abandon()

        controls = self.controls()

        self.assertIn('Reopen', controls)
        self.assertNotIn('Abandon', controls)

    def test_a_completed_request_offers_neither(self):
        self.cr.status = ChangeRequestStatusChoices.COMPLETED
        self.cr.save(update_fields=['status'])

        controls = self.controls()

        self.assertNotIn('Abandon', controls)
        self.assertNotIn('Reopen', controls)

    def test_submit_for_review_stays_with_the_policies(self):
        """
        Submitting is what attaches the policies, so it is the one action that does belong in
        that card, directly under the list it populates.
        """
        self.assertIn('Submit for review', self.policies_card())
