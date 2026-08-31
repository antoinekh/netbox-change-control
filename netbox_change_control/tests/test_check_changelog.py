"""
Check results belong in the changelog.

A required check going from failed to passed is what opens the merge gate. Results were
written with a queryset update, which goes straight to the database and fires no post_save, so
NetBox recorded nothing: the plugin whose job is the record of who allowed what kept no record
of the machine half of that decision.
"""

import uuid

from core.models import ObjectChange
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox.context_managers import event_tracking
from users.models import Group, User

from netbox_change_control.checks import CheckResult, register_check, run_checks
from netbox_change_control.checks import _registry as check_registry
from netbox_change_control.choices import MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy, PolicyRule
from netbox_change_control.tests.base import make_branch


class CheckResultsAreLoggedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.group = Group.objects.create(name='Engineers')
        cls.policy = Policy.objects.create(name='Probe policy', checks=['probe'])
        PolicyRule.objects.create(policy=cls.policy, name='One engineer', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        from django.test import RequestFactory

        self.outcome = CheckResult.failed('not yet')
        register_check('probe', 'Probe', lambda cr: self.outcome, scope='policy')
        self.addCleanup(check_registry.pop, 'probe', None)

        self.branch = make_branch('log', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)

        self.request = RequestFactory().post('/')
        self.request.user = self.requester
        # ObjectChange.request_id is not nullable, and NetBox normally gets this from its
        # request middleware.
        self.request.id = uuid.uuid4()

    def changes_for(self, check):
        return ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(MergeCheck),
            changed_object_id=check.pk,
        )

    def test_a_result_that_moves_is_recorded(self):
        check = MergeCheck.objects.get(change_request=self.cr, name='probe')
        self.assertEqual(check.status, MergeCheckStatusChoices.FAILURE)
        before = self.changes_for(check).count()

        self.outcome = CheckResult.passed('all good')
        with event_tracking(self.request):
            run_checks(self.cr)

        check.refresh_from_db()
        self.assertEqual(check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertGreater(self.changes_for(check).count(), before)

    def test_a_re_run_finding_the_same_answer_records_nothing(self):
        """
        Checks re-run on many signals. Logging every run would bury the transitions that
        matter under identical entries.
        """
        check = MergeCheck.objects.get(change_request=self.cr, name='probe')
        with event_tracking(self.request):
            run_checks(self.cr)
        before = self.changes_for(check).count()

        with event_tracking(self.request):
            run_checks(self.cr)

        self.assertEqual(self.changes_for(check).count(), before)

    def test_the_stored_result_is_still_correct(self):
        self.outcome = CheckResult.passed('all good')
        run_checks(self.cr)

        check = MergeCheck.objects.get(change_request=self.cr, name='probe')
        self.assertEqual(check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertEqual(check.summary, 'all good')
        self.assertIsNotNone(check.completed)

    def test_completed_moves_only_when_the_result_moves(self):
        run_checks(self.cr)
        check = MergeCheck.objects.get(change_request=self.cr, name='probe')
        first = check.completed

        run_checks(self.cr)
        check.refresh_from_db()
        self.assertEqual(check.completed, first)

        self.outcome = CheckResult.passed('all good')
        run_checks(self.cr)
        check.refresh_from_db()
        self.assertGreater(check.completed, first)
