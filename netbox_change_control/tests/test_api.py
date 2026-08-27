"""
REST API tests.

These matter most for the check-reporting flow, which is how a CI pipeline talks to the
plugin and is otherwise only covered by hand.
"""

from django.urls import reverse
from netbox_branching.models import Branch
from rest_framework import status
from users.models import Group, ObjectPermission, User
from utilities.testing import APITestCase

from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)


class ChangeControlAPITestCase(APITestCase):
    """
    APITestCase gives us a non-superuser account plus token auth, so these tests exercise
    the permission layer rather than bypassing it.
    """

    def _grant(self, actions, *models):
        from django.contrib.contenttypes.models import ContentType

        permission = ObjectPermission.objects.create(
            name=f'test-{"-".join(actions)}-{id(models)}', actions=list(actions)
        )
        permission.users.add(self.user)
        permission.object_types.set([ContentType.objects.get_for_model(model) for model in models])
        return permission


class PolicyAPITest(ChangeControlAPITestCase):
    def test_listing_policies_requires_permission(self):
        url = reverse('plugins-api:netbox_change_control-api:policy-list')
        self.assertHttpStatus(self.client.get(url, **self.header), status.HTTP_403_FORBIDDEN)

    def test_listing_policies(self):
        Policy.objects.create(name='Baseline')
        self._grant(['view'], Policy)
        url = reverse('plugins-api:netbox_change_control-api:policy-list')
        response = self.client.get(url, **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_creating_a_policy(self):
        self._grant(['view', 'add'], Policy)
        url = reverse('plugins-api:netbox_change_control-api:policy-list')
        response = self.client.post(url, {'name': 'Created via API', 'weight': 50}, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertTrue(Policy.objects.filter(name='Created via API').exists())

    def test_creating_a_rule(self):
        policy = Policy.objects.create(name='P')
        group = Group.objects.create(name='Engineers')
        self._grant(['view', 'add'], PolicyRule)
        self._grant(['view'], Policy)

        url = reverse('plugins-api:netbox_change_control-api:policyrule-list')
        response = self.client.post(
            url,
            {'policy': policy.pk, 'name': 'Two engineers', 'min_reviews': 2, 'groups': [group.pk]},
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        rule = PolicyRule.objects.get(name='Two engineers')
        self.assertEqual(rule.min_reviews, 2)
        self.assertEqual(list(rule.groups.all()), [group])


class ChangeRequestAPITest(ChangeControlAPITestCase):
    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='api-branch')
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='API request', requester=self.requester)

    def test_retrieving_a_change_request(self):
        self._grant(['view'], ChangeRequest)
        url = reverse('plugins-api:netbox_change_control-api:changerequest-detail', args=[self.cr.pk])
        response = self.client.get(url, **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'API request')
        self.assertFalse(response.data['approved'])

    def test_the_response_exposes_the_preserved_branch_name(self):
        self._grant(['view'], ChangeRequest)
        url = reverse('plugins-api:netbox_change_control-api:changerequest-detail', args=[self.cr.pk])

        response = self.client.get(url, **self.header)
        self.assertEqual(response.data['branch_name'], 'api-branch')
        self.assertFalse(response.data['branch_deleted'])

        self.branch.delete()
        response = self.client.get(url, **self.header)
        self.assertIsNone(response.data['branch'])
        self.assertEqual(response.data['branch_name'], 'api-branch')
        self.assertTrue(response.data['branch_deleted'])

    def test_setting_a_change_window(self):
        self._grant(['view', 'change'], ChangeRequest)
        url = reverse('plugins-api:netbox_change_control-api:changerequest-detail', args=[self.cr.pk])
        response = self.client.patch(
            url,
            {'scheduled_start': '2030-01-01T00:00:00Z', 'auto_merge': True},
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.cr.refresh_from_db()
        self.assertTrue(self.cr.auto_merge)
        self.assertEqual(self.cr.window_state(), 'early')


class MergeCheckAPITest(ChangeControlAPITestCase):
    """
    The flow a CI pipeline uses: find the check by name, then report a result.
    """

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='ci-branch')
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='CI request', requester=self.requester)
        self.check = MergeCheck.objects.create(
            change_request=self.cr, name='ci-pipeline', label='CI pipeline', required=True
        )

    def test_finding_a_check_by_request_and_name(self):
        self._grant(['view'], MergeCheck)
        url = reverse('plugins-api:netbox_change_control-api:mergecheck-list')
        response = self.client.get(f'{url}?change_request_id={self.cr.pk}&name=ci-pipeline', **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.check.pk)

    def test_reporting_a_result(self):
        self._grant(['view', 'change'], MergeCheck)
        url = reverse('plugins-api:netbox_change_control-api:mergecheck-detail', args=[self.check.pk])
        response = self.client.patch(
            url,
            {
                'status': 'success',
                'summary': '42 tests passed',
                'details_url': 'https://ci.example.com/build/99',
            },
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertEqual(self.check.summary, '42 tests passed')

    def test_a_reported_result_is_reflected_in_blocks_merge(self):
        self._grant(['view', 'change'], MergeCheck)
        self.assertTrue(self.check.blocks_merge)

        url = reverse('plugins-api:netbox_change_control-api:mergecheck-detail', args=[self.check.pk])
        self.client.patch(url, {'status': 'success'}, format='json', **self.header)

        self.check.refresh_from_db()
        self.assertFalse(self.check.blocks_merge)

    def test_reporting_requires_change_permission(self):
        self._grant(['view'], MergeCheck)
        url = reverse('plugins-api:netbox_change_control-api:mergecheck-detail', args=[self.check.pk])
        response = self.client.patch(url, {'status': 'success'}, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_403_FORBIDDEN)


class ReviewAPITest(ChangeControlAPITestCase):
    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='review-branch')
        self.requester = User.objects.create(username='requester')
        self.reviewer = User.objects.create(username='reviewer')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)

    def test_creating_a_review(self):
        self._grant(['view', 'add'], Review)
        url = reverse('plugins-api:netbox_change_control-api:review-list')
        response = self.client.post(
            url,
            {
                'change_request': self.cr.pk,
                'decision': ReviewDecisionChoices.APPROVE,
                'comment': 'looks good',
            },
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        # The reviewer is the caller, never a value from the payload. See ReviewAttributionTest.
        self.assertEqual(Review.objects.get(change_request=self.cr).reviewer, self.user)

    def test_a_second_review_by_the_same_user_is_rejected(self):
        # Attributed to the caller, because that is who the second POST would be attributed to.
        Review.objects.create(change_request=self.cr, reviewer=self.user, decision=ReviewDecisionChoices.APPROVE)
        self._grant(['view', 'add'], Review)
        url = reverse('plugins-api:netbox_change_control-api:review-list')
        response = self.client.post(
            url,
            {
                'change_request': self.cr.pk,
                'decision': ReviewDecisionChoices.REJECT,
                'comment': 'changed my mind',
            },
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)


class ChangeCommentAPITest(ChangeControlAPITestCase):
    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='comment-branch')
        self.requester = User.objects.create(username='requester')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)

    def test_listing_comments_is_empty_by_default(self):
        self._grant(['view'], ChangeComment)
        url = reverse('plugins-api:netbox_change_control-api:changecomment-list')
        response = self.client.get(url, **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)


class ReviewAttributionTest(ChangeControlAPITestCase):
    """
    A review is a personal statement. The API must record it as the caller.

    The object form stopped offering `reviewer` for this reason, but the API still accepted
    one, so any token holding `add_review` could post an approval attributed to a colleague
    and satisfy a rule that colleague was the only eligible member of.
    """

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='review-attribution')
        self.requester = User.objects.create(username='attribution-requester')
        self.colleague = User.objects.create(username='colleague')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)

    def test_a_review_is_recorded_as_the_caller(self):
        self._grant(['view', 'add'], Review)
        url = reverse('plugins-api:netbox_change_control-api:review-list')
        response = self.client.post(
            url,
            {'change_request': self.cr.pk, 'decision': 'approve', 'comment': 'fine'},
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(Review.objects.get(pk=response.data['id']).reviewer, self.user)

    def test_naming_somebody_else_is_ignored(self):
        self._grant(['view', 'add'], Review)
        url = reverse('plugins-api:netbox_change_control-api:review-list')
        response = self.client.post(
            url,
            {
                'change_request': self.cr.pk,
                'reviewer': self.colleague.pk,
                'decision': 'approve',
                'comment': 'not mine to give',
            },
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        review = Review.objects.get(pk=response.data['id'])
        self.assertEqual(review.reviewer, self.user)
        self.assertNotEqual(review.reviewer, self.colleague)


class CheckRequirednessTest(ChangeControlAPITestCase):
    """
    A reporter cannot decide whether its own check counts.
    """

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(name='check-required')
        self.requester = User.objects.create(username='required-requester')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        self.check = MergeCheck.objects.create(
            change_request=self.cr,
            name='ci',
            label='CI',
            required=True,
            status=MergeCheckStatusChoices.PENDING,
        )

    def test_required_cannot_be_written(self):
        self._grant(['view', 'change'], MergeCheck)
        url = reverse('plugins-api:netbox_change_control-api:mergecheck-detail', args=[self.check.pk])
        response = self.client.patch(url, {'required': False}, format='json', **self.header)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.check.refresh_from_db()
        self.assertTrue(self.check.required)

    def test_a_result_can_still_be_reported(self):
        self._grant(['view', 'change'], MergeCheck)
        url = reverse('plugins-api:netbox_change_control-api:mergecheck-detail', args=[self.check.pk])
        response = self.client.patch(
            url,
            {'status': 'success', 'summary': '42 tests passed'},
            format='json',
            **self.header,
        )
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertEqual(self.check.summary, '42 tests passed')
