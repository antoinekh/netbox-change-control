"""
A change request merges itself only if its requester may merge the branch.

The automatic merge runs as the requester, and nothing checked that the requester held
netbox-branching's `merge` permission, so somebody allowed to edit change requests but not to
merge could tick "Merge automatically" and have the branch merged anyway.
"""

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from netbox_branching.jobs import MergeBranchJob
from users.models import User

from netbox_change_control.automerge import try_auto_merge
from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import grant_merge, make_branch

MESSAGE = 'cannot merge this branch'


def queued_merges(branch):
    from core.choices import JobStatusChoices

    return MergeBranchJob.get_jobs(branch).filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES)


class AutoMergePermissionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='auto-requester')
        cls.policy = Policy.objects.create(name='Automatic')
        PolicyRule.objects.create(policy=cls.policy, name='None needed', min_reviews=0)

    def _request(self, suffix, **kwargs):
        branch = make_branch('auto-perm', f'{self._testMethodName}-{suffix}')
        change_request = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, **kwargs)
        ChangeRequestPolicy.objects.create(change_request=change_request, policy=self.policy)
        return change_request

    def _approved(self, suffix='x'):
        change_request = self._request(suffix, auto_merge=True)
        change_request.submit()
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, ChangeRequestStatusChoices.APPROVED)
        return change_request

    def test_a_requester_who_cannot_merge_gets_no_merge(self):
        change_request = self._approved()
        self.assertFalse(try_auto_merge(change_request))
        self.assertEqual(queued_merges(change_request.branch).count(), 0)

    def test_a_requester_who_can_merge_gets_one(self):
        grant_merge(self.requester)
        change_request = self._approved()
        self.assertEqual(queued_merges(change_request.branch).count(), 1)

    def test_a_constraint_on_the_merge_permission_is_honoured(self):
        grant_merge(self.requester, {'name': 'some-other-branch'})
        change_request = self._approved()
        self.assertEqual(queued_merges(change_request.branch).count(), 0)

    def test_the_flag_is_refused_for_a_requester_who_cannot_merge(self):
        change_request = self._request('flag', auto_merge=True)
        with self.assertRaisesMessage(ValidationError, MESSAGE):
            change_request.full_clean()

    def test_the_flag_is_accepted_for_a_requester_who_can_merge(self):
        grant_merge(self.requester)
        self._request('flag', auto_merge=True).full_clean()

    def test_a_request_without_the_flag_needs_no_merge_permission(self):
        self._request('plain').full_clean()

    def test_the_api_refuses_the_flag(self):
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission

        change_request = self._request('api')
        editor = User.objects.create(username='auto-editor')
        permission = ObjectPermission.objects.create(name='edit-crs', actions=['view', 'change'])
        permission.object_types.add(ContentType.objects.get_for_model(ChangeRequest))
        permission.users.add(editor)
        client = Client()
        client.force_login(editor)

        response = client.patch(
            f'/api/plugins/change-control/change-requests/{change_request.pk}/',
            {'auto_merge': True},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        change_request.refresh_from_db()
        self.assertFalse(change_request.auto_merge)

    def test_the_page_says_why_a_flagged_request_will_not_merge_itself(self):
        change_request = self._request('page', auto_merge=True)
        admin = User.objects.create(username='auto-admin', is_superuser=True)
        client = Client()
        client.force_login(admin)

        page = client.get(change_request.get_absolute_url()).content.decode()

        self.assertIn('This change will not merge automatically.', page)
