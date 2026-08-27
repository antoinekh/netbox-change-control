"""
Tests for pre-merge checks.
"""

from django.test import TestCase
from users.models import User

from netbox_change_control.checks import (
    CheckResult,
    _registry,
    register_check,
    run_checks,
    sync_checks,
)
from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy
from netbox_change_control.tests.base import ChangeControlTestCase, make_branch
from netbox_change_control.validators import require_approved_change_request


class CheckTestCase(ChangeControlTestCase):
    """
    An approved request, with the check registry isolated so a test which registers a check
    cannot leak into another.
    """

    branch_prefix = 'chk'
    approved = True
    # The registry is emptied below, so the fixture policy must not ask for checks which are
    # no longer registered. A test which registers one calls require_checks for it.
    policy_checks = ()

    def setUp(self):
        # Clear before the fixture is built: creating a change request runs the checks.
        self._saved_registry = dict(_registry)
        _registry.clear()
        super().setUp()

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved_registry)


class RegisteredCheckTest(CheckTestCase):
    def test_run_checks_records_a_pass(self):
        register_check('always-ok', 'Always OK', lambda cr: CheckResult.passed('fine'))
        run_checks(self.cr)
        check = MergeCheck.objects.get(change_request=self.cr, name='always-ok')
        self.assertEqual(check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertEqual(check.summary, 'fine')
        self.assertIsNotNone(check.completed)

    def test_a_raising_check_is_recorded_as_errored(self):
        """
        A broken check must not take the page down with it.
        """

        def boom(cr):
            raise RuntimeError('exploded')

        register_check('broken', 'Broken', boom)
        run_checks(self.cr)
        check = MergeCheck.objects.get(change_request=self.cr, name='broken')
        self.assertEqual(check.status, MergeCheckStatusChoices.ERROR)
        self.assertIn('exploded', check.summary)

    def test_sync_removes_checks_which_no_longer_exist(self):
        register_check('temp', 'Temporary', lambda cr: CheckResult.passed())
        sync_checks(self.cr)
        self.assertTrue(MergeCheck.objects.filter(change_request=self.cr, name='temp').exists())

        _registry.clear()
        sync_checks(self.cr)
        self.assertFalse(MergeCheck.objects.filter(change_request=self.cr, name='temp').exists())


class CheckGateTest(CheckTestCase):
    def _approve_status(self):
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_a_failing_required_check_blocks_the_merge(self):
        self._approve_status()
        register_check('must-pass', 'Must pass', lambda cr: CheckResult.failed('nope'))
        run_checks(self.cr)

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Required checks are not passing', indicator.message)
        self.assertIn('Must pass', indicator.message)

    def test_a_failing_optional_check_does_not_block(self):
        self._approve_status()
        register_check('advisory', 'Advisory', lambda cr: CheckResult.failed('nope'), required=False)
        run_checks(self.cr)

        self.assertTrue(require_approved_change_request(self.branch).permitted)

    def test_a_pending_required_check_blocks_the_merge(self):
        """
        An external check that has never reported must block, otherwise declaring it would be
        pointless.
        """
        self._approve_status()
        MergeCheck.objects.create(
            change_request=self.cr,
            name='external-ci',
            label='External CI',
            required=True,
            status=MergeCheckStatusChoices.PENDING,
        )
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('External CI', indicator.message)

    def test_a_skipped_check_clears_the_gate(self):
        self._approve_status()
        register_check('n-a', 'Not applicable', lambda cr: CheckResult.skipped('nothing to do'))
        run_checks(self.cr)
        self.assertTrue(require_approved_change_request(self.branch).permitted)

    def test_passing_checks_clear_the_gate(self):
        self._approve_status()
        register_check('ok', 'OK', lambda cr: CheckResult.passed())
        run_checks(self.cr)
        self.assertTrue(require_approved_change_request(self.branch).permitted)


class BuiltinCheckTest(CheckTestCase):
    def test_empty_branch_fails_the_has_changes_check(self):
        from netbox_change_control.checks import check_branch_has_changes

        result = check_branch_has_changes(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.FAILURE)
        self.assertIn('no changes', result.summary)

    def test_no_conflicts_passes_on_a_clean_branch(self):
        from netbox_change_control.checks import check_no_conflicts

        result = check_no_conflicts(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.SUCCESS)


class BuiltinSelectionTest(TestCase):
    """
    enable_builtin_checks accepts True, False, a list of names, or a mapping of name to scope.
    Scoping is covered by BuiltinScopeTest.
    """

    def setUp(self):
        self._saved = dict(_registry)
        _registry.clear()

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved)

    def test_none_registers_every_builtin(self):
        from netbox_change_control.checks import BUILTIN_CHECKS, register_builtin_checks

        selected = register_builtin_checks(None)
        self.assertCountEqual(selected, BUILTIN_CHECKS.keys())
        self.assertCountEqual(_registry.keys(), BUILTIN_CHECKS.keys())

    def test_a_list_registers_only_that_subset(self):
        from netbox_change_control.checks import register_builtin_checks

        selected = register_builtin_checks(['no-conflicts', 'threads-resolved'])
        self.assertCountEqual(selected, ['no-conflicts', 'threads-resolved'])
        self.assertCountEqual(_registry.keys(), ['no-conflicts', 'threads-resolved'])

    def test_an_unknown_name_is_skipped_not_raised(self):
        """
        A typo in configuration must not stop NetBox from booting.
        """
        from netbox_change_control.checks import register_builtin_checks

        selected = register_builtin_checks(['no-conflicts', 'nonsense'])
        self.assertEqual(list(selected), ['no-conflicts'])


class ThreadsResolvedCheckTest(CheckTestCase):
    """
    The threads-resolved built-in blocks a merge while a comment thread is open.
    """

    def _diff(self):

        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        return ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='circuit-1',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )

    def _thread(self):
        from netbox_change_control.models import ChangeComment

        return ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=self._diff(),
            author=self.reviewer,
            text='Is this right?',
        )

    def test_passes_when_there_are_no_threads(self):
        from netbox_change_control.checks import check_threads_resolved

        result = check_threads_resolved(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.SUCCESS)

    def test_fails_while_a_thread_is_open(self):
        from netbox_change_control.checks import check_threads_resolved

        self._thread()
        result = check_threads_resolved(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.FAILURE)
        # ChangeDiff.save() rewrites object_repr from the live object, so assert on the
        # count rather than on a repr this test cannot control.
        self.assertIn('1 unresolved comment thread', result.summary)

    def test_passes_once_the_thread_is_resolved(self):
        from netbox_change_control.checks import check_threads_resolved

        thread = self._thread()
        thread.resolved = True
        thread.save(update_fields=['resolved'])
        result = check_threads_resolved(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.SUCCESS)

    def test_a_reply_alone_does_not_keep_the_thread_open(self):
        """
        Only thread roots count. A reply is part of its parent thread, not a new concern.
        """
        from netbox_change_control.checks import check_threads_resolved
        from netbox_change_control.models import ChangeComment

        thread = self._thread()
        ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=thread.change_diff,
            parent=thread,
            author=self.requester,
            text='Yes it is.',
        )
        thread.resolved = True
        thread.save(update_fields=['resolved'])
        self.assertEqual(check_threads_resolved(self.cr).status, MergeCheckStatusChoices.SUCCESS)

    def test_an_open_thread_blocks_the_merge(self):
        from netbox_change_control.checks import register_builtin_checks

        register_builtin_checks(['threads-resolved'])
        self.require_checks('threads-resolved')
        self._thread()
        run_checks(self.cr)

        self.cr.refresh_from_db()
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Comment threads resolved', indicator.message)

    def test_resolving_the_thread_reruns_the_check(self):
        """
        Resolving a thread must refresh the check, or its result goes stale.
        """
        from netbox_change_control.checks import register_builtin_checks

        register_builtin_checks(['threads-resolved'])
        self.require_checks('threads-resolved')
        thread = self._thread()
        run_checks(self.cr)
        self.assertEqual(
            MergeCheck.objects.get(change_request=self.cr, name='threads-resolved').status,
            MergeCheckStatusChoices.FAILURE,
        )

        thread.resolved = True
        thread.save(update_fields=['resolved'])

        self.assertEqual(
            MergeCheck.objects.get(change_request=self.cr, name='threads-resolved').status,
            MergeCheckStatusChoices.SUCCESS,
        )


class MissingCheckRowTest(CheckTestCase):
    """
    A check registered after a change request was created has no stored row.

    Reading the stored rows alone would let that request merge past a check nobody ran, so
    the gate computes from the registry and treats a missing row as blocking.
    """

    def test_a_newly_registered_check_blocks_a_request_with_no_row(self):
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.cr.checks.all().delete()
        self.assertTrue(require_approved_change_request(self.branch).permitted)

        register_check('added-later', 'Added later', lambda cr: CheckResult.passed())

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Added later (not run)', indicator.message)

    def test_running_the_check_creates_the_row_and_clears_the_gate(self):
        self.cr.checks.all().delete()
        register_check('added-later', 'Added later', lambda cr: CheckResult.passed())
        run_checks(self.cr)

        self.assertTrue(MergeCheck.objects.filter(change_request=self.cr, name='added-later').exists())
        self.assertTrue(require_approved_change_request(self.branch).permitted)

    def test_an_advisory_check_with_no_row_does_not_block(self):
        self.cr.checks.all().delete()
        register_check('advice', 'Advice', lambda cr: CheckResult.failed('x'), required=False)
        self.assertTrue(require_approved_change_request(self.branch).permitted)


class GateTrustsConfigurationNotRowsTest(CheckTestCase):
    """
    Whether a check is required comes from the registry and the plugin configuration, never
    from the stored row. A reporter holding an API token must not be able to neutralise a
    required check by flipping `required` on its own row.
    """

    def test_flipping_required_on_the_row_does_not_bypass_the_gate(self):
        register_check('must-pass', 'Must pass', lambda cr: CheckResult.failed('nope'))
        run_checks(self.cr)
        self.assertFalse(require_approved_change_request(self.branch).permitted)

        MergeCheck.objects.filter(change_request=self.cr, name='must-pass').update(required=False)

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Must pass', indicator.message)

    def test_deleting_the_row_does_not_bypass_the_gate(self):
        register_check('must-pass', 'Must pass', lambda cr: CheckResult.failed('nope'))
        run_checks(self.cr)
        MergeCheck.objects.filter(change_request=self.cr, name='must-pass').delete()

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Must pass (not run)', indicator.message)


class ThreadsCheckAfterBranchDeletionTest(CheckTestCase):
    """
    Once the branch is gone its ChangeDiff rows go with it, so a comment's change_diff is
    null. Reaching through that relation recorded the check as errored.
    """

    def test_the_check_reports_the_stored_label_not_a_crash(self):

        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.checks import check_threads_resolved
        from netbox_change_control.models import ChangeComment

        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self.policy.pk,
            object_repr='some-object',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        comment = ChangeComment.objects.create(
            change_request=self.cr,
            change_diff=diff,
            author=self.reviewer,
            text='Wait',
        )
        label = comment.change_label

        self.branch.delete()
        self.cr.refresh_from_db()

        result = check_threads_resolved(self.cr)
        self.assertEqual(result.status, MergeCheckStatusChoices.FAILURE)
        self.assertIn(label, result.summary)


class AutoMergeOnCheckPassTest(CheckTestCase):
    """
    run_checks writes results with .update(), which fires no post_save. Auto-merge must still
    fire when an in-process check turns green.
    """

    def test_a_passing_check_triggers_auto_merge(self):
        from unittest.mock import patch

        from netbox_branching.jobs import MergeBranchJob

        self.cr.auto_merge = True
        self.cr.save()
        register_check('ok', 'OK', lambda cr: CheckResult.passed())

        with patch.object(MergeBranchJob, 'enqueue') as enqueue:
            run_checks(self.cr)
            enqueue.assert_called_once()

    def test_a_failing_check_does_not_trigger_auto_merge(self):
        from unittest.mock import patch

        from netbox_branching.jobs import MergeBranchJob

        self.cr.auto_merge = True
        self.cr.save()
        register_check('bad', 'Bad', lambda cr: CheckResult.failed('no'))

        with patch.object(MergeBranchJob, 'enqueue') as enqueue:
            run_checks(self.cr)
            enqueue.assert_not_called()


class ChecksRunOnCreationTest(TestCase):
    """
    A new change request must arrive with its checks already run.

    Creating the rows without running them left every new request showing four checks stuck
    on "pending", which reads as broken and blocks the merge on checks nobody was asked to
    run.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='creator')

    def setUp(self):
        self._saved = dict(_registry)
        _registry.clear()
        register_check('probe', 'Probe', lambda cr: CheckResult.passed('ran'))
        self.branch = make_branch('new', self._testMethodName)

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved)

    def test_a_new_request_has_its_checks_run(self):
        cr = ChangeRequest.objects.create(branch=self.branch, title='Fresh', requester=self.requester)
        check = MergeCheck.objects.get(change_request=cr, name='probe')
        self.assertEqual(check.status, MergeCheckStatusChoices.SUCCESS)
        self.assertEqual(check.summary, 'ran')
        self.assertIsNotNone(check.completed)

    def test_no_check_is_left_pending(self):
        cr = ChangeRequest.objects.create(branch=self.branch, title='Fresh', requester=self.requester)
        pending = cr.checks.filter(status=MergeCheckStatusChoices.PENDING)
        self.assertEqual(list(pending), [])


class ChecksRefreshOnApprovalTest(CheckTestCase):
    """
    Reaching Approved is the moment a merge becomes possible, so the checks must be current.

    A branch edit invalidates the reviews but not the stored check results. Without a refresh
    at this point a request could be edited to introduce a conflict, re-approved, and merged
    against a stale "no conflicts" row.
    """

    def test_reaching_approved_reruns_the_checks(self):
        calls = []

        def counting_check(change_request):
            calls.append(change_request.pk)
            return CheckResult.passed()

        register_check('counter', 'Counter', counting_check)

        # The request is already approved from the base fixture; force a transition.
        self.cr.status = ChangeRequestStatusChoices.NEEDS_REVIEW
        self.cr.save(update_fields=['status'])
        calls.clear()

        from netbox_change_control.policy import refresh_status

        refresh_status(self.cr)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.assertEqual(calls, [self.cr.pk], 'the check should run exactly once on approval')


class PolicyScopedCheckTest(CheckTestCase):
    """
    A check does not have to apply everywhere.

    Registering a check globally puts it on every change request, which is wrong for anything
    specific: a peer sign-off that only matters for circuits would otherwise appear on an
    IPAM change and have to be skipped by hand. A policy-scoped check applies only where a
    policy asks for it.
    """

    def _register_scoped(self, name='peer-signoff', result=None):
        from netbox_change_control.checks import CheckScope, register_check

        register_check(
            name,
            'Peer sign-off',
            lambda cr: result or CheckResult.passed('signed'),
            scope=CheckScope.POLICY,
        )

    def test_a_scoped_check_is_absent_when_no_policy_asks_for_it(self):
        self._register_scoped()
        run_checks(self.cr)
        self.assertFalse(self.cr.checks.filter(name='peer-signoff').exists())

    def test_a_policy_naming_it_brings_it_in(self):
        self._register_scoped()
        self.policy.checks = ['peer-signoff']
        self.policy.save()

        run_checks(self.cr)
        check = self.cr.checks.get(name='peer-signoff')
        self.assertEqual(check.label, 'Peer sign-off')
        self.assertEqual(check.status, MergeCheckStatusChoices.SUCCESS)

    def test_a_scoped_check_which_no_policy_asks_for_never_runs(self):
        """
        Absent from the rows is not enough: the function must not be called either.
        """
        calls = []

        from netbox_change_control.checks import CheckScope, register_check

        def counting(change_request):
            calls.append(change_request.pk)
            return CheckResult.passed()

        register_check('counting', 'Counting', counting, scope=CheckScope.POLICY)
        run_checks(self.cr)
        self.assertEqual(calls, [])

    def test_a_failing_scoped_check_blocks_the_merge(self):
        from netbox_change_control.checks import CheckScope, register_check

        register_check(
            'peer-signoff',
            'Peer sign-off',
            lambda cr: CheckResult.failed('nobody signed'),
            scope=CheckScope.POLICY,
        )
        self.policy.checks = ['peer-signoff']
        self.policy.save()
        run_checks(self.cr)

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('Peer sign-off', indicator.message)

    def test_an_unregistered_name_becomes_an_externally_reported_check(self):
        """
        A policy can require a result from a pipeline without that pipeline being wired into
        every change request, the same way required_external_checks works globally.
        """
        self.policy.checks = ['cab-approval']
        self.policy.save()
        run_checks(self.cr)

        check = self.cr.checks.get(name='cab-approval')
        self.assertEqual(check.status, MergeCheckStatusChoices.PENDING)
        self.assertTrue(check.required)
        self.assertIn('cab-approval', require_approved_change_request(self.branch).message)

    def test_detaching_the_policy_removes_its_check(self):
        self._register_scoped()
        self.policy.checks = ['peer-signoff']
        self.policy.save()
        run_checks(self.cr)
        self.assertTrue(self.cr.checks.filter(name='peer-signoff').exists())

        ChangeRequestPolicy.objects.filter(change_request=self.cr).delete()
        run_checks(self.cr)
        self.assertFalse(self.cr.checks.filter(name='peer-signoff').exists())

    def test_a_global_check_still_applies_everywhere(self):
        from netbox_change_control.checks import register_check

        register_check('everywhere', 'Everywhere', lambda cr: CheckResult.passed())
        run_checks(self.cr)
        self.assertTrue(self.cr.checks.filter(name='everywhere').exists())

    def test_an_unknown_scope_is_refused_at_registration(self):
        from netbox_change_control.checks import register_check

        with self.assertRaises(ValueError):
            register_check('bad', 'Bad', lambda cr: CheckResult.passed(), scope='sometimes')


class PolicyChecksFormTest(TestCase):
    """
    The policy form offers the registered opt-in checks as a list, and keeps a text box for
    names reported from outside, which cannot be offered because nothing declares them first.
    """

    def setUp(self):
        from netbox_change_control.checks import _registry

        self._saved = dict(_registry)
        _registry.clear()
        self.policy = Policy.objects.create(name='Form policy')

    def tearDown(self):
        from netbox_change_control.checks import _registry

        _registry.clear()
        _registry.update(self._saved)

    def _register(self, name='peer-signoff', label='Peer sign-off'):
        from netbox_change_control.checks import CheckScope, register_check

        register_check(name, label, lambda cr: CheckResult.passed(), scope=CheckScope.POLICY)

    def _form(self, **data):
        from netbox_change_control.forms import PolicyForm

        payload = {'name': self.policy.name, 'weight': 1000, 'enabled': True}
        payload.update(data)
        return PolicyForm(payload, instance=self.policy)

    def test_only_opt_in_checks_are_offered(self):
        from netbox_change_control.checks import register_check
        from netbox_change_control.forms import PolicyForm

        self._register()
        register_check('everywhere', 'Everywhere', lambda cr: CheckResult.passed())

        offered = dict(PolicyForm(instance=self.policy).fields['checks'].choices)
        self.assertIn('peer-signoff', offered)
        self.assertNotIn('everywhere', offered, 'a check which already applies everywhere is not opt-in')
        self.assertEqual(offered['peer-signoff'], 'Peer sign-off (peer-signoff)')

    def test_the_two_fields_are_folded_into_one_list(self):
        self._register()
        form = self._form(checks=['peer-signoff'], external_checks='cab-approval, ticket ')
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.checks, ['cab-approval', 'peer-signoff', 'ticket'])

    def test_a_stored_name_lands_in_exactly_one_field(self):
        from netbox_change_control.forms import PolicyForm

        self._register()
        self.policy.checks = ['peer-signoff', 'cab-approval']
        self.policy.save()

        form = PolicyForm(instance=self.policy)
        self.assertEqual(form.initial['checks'], ['peer-signoff'])
        self.assertEqual(form.initial['external_checks'], 'cab-approval')

    def test_an_unregistered_check_moves_to_the_text_box(self):
        """
        A check removed from the code must not vanish from the policy on the next save.
        """
        from netbox_change_control.forms import PolicyForm

        self.policy.checks = ['peer-signoff']
        self.policy.save()

        form = PolicyForm(instance=self.policy)
        self.assertEqual(form.initial['checks'], [])
        self.assertEqual(form.initial['external_checks'], 'peer-signoff')

    def test_an_empty_form_clears_the_list(self):
        self._register()
        self.policy.checks = ['peer-signoff']
        self.policy.save()

        form = self._form(checks=[], external_checks='')
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.checks, [])


class BuiltinScopeTest(TestCase):
    """
    A built-in is registered, never applied, until a policy names it.

    Sites differ on which checks belong where. `threads-resolved` blocking every merge is
    right for some teams and heavy-handed for others, and the answer usually depends on what
    the change touches, which is exactly what a policy expresses. A check wanted everywhere is
    a policy with no object types.
    """

    def setUp(self):
        from netbox_change_control.checks import _registry

        self._saved = dict(_registry)
        _registry.clear()

    def tearDown(self):
        from netbox_change_control.checks import _registry

        _registry.clear()
        _registry.update(self._saved)

    def test_every_builtin_is_registered_for_policies_to_choose(self):
        """
        Registering makes a check available. A policy decides where it applies, so there is
        one mechanism rather than a global switch and a per-policy one that can disagree.
        """
        from netbox_change_control.checks import (
            BUILTIN_CHECKS,
            CheckScope,
            get_registered_checks,
            register_builtin_checks,
        )

        register_builtin_checks()
        registered = get_registered_checks()
        self.assertEqual(sorted(registered), sorted(BUILTIN_CHECKS))
        self.assertTrue(all(c.scope == CheckScope.POLICY for c in registered.values()))

    def test_a_list_selects_a_subset(self):
        from netbox_change_control.checks import CheckScope, get_registered_checks, register_builtin_checks

        selected = register_builtin_checks(['no-conflicts'])
        self.assertEqual(selected, ['no-conflicts'])
        self.assertEqual(sorted(get_registered_checks()), ['no-conflicts'])
        self.assertEqual(get_registered_checks()['no-conflicts'].scope, CheckScope.POLICY)

    def test_every_builtin_is_offered_on_the_policy_form(self):
        from netbox_change_control.checks import BUILTIN_CHECKS, register_builtin_checks
        from netbox_change_control.forms import PolicyForm

        register_builtin_checks()
        offered = dict(PolicyForm().fields['checks'].choices)
        self.assertEqual(sorted(offered), sorted(BUILTIN_CHECKS))

    def test_a_builtin_only_runs_where_a_policy_asks(self):
        from netbox_change_control.checks import register_builtin_checks

        register_builtin_checks(['threads-resolved'])

        branch = make_branch('bscope', self._testMethodName)
        requester = User.objects.create(username='builtin-scope-requester')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=requester)
        run_checks(cr)
        self.assertFalse(cr.checks.filter(name='threads-resolved').exists())

        policy = Policy.objects.create(name='Wants threads resolved', checks=['threads-resolved'])
        ChangeRequestPolicy.objects.create(change_request=cr, policy=policy)
        run_checks(cr)
        self.assertTrue(cr.checks.filter(name='threads-resolved').exists())


class ChecksRunWhenPoliciesAttachTest(TestCase):
    """
    A change request is created before its policies are matched, and every built-in check is
    policy-scoped. The run at creation therefore sees no policies and no checks, so unless
    attaching a policy runs them the panel sits at "pending" until somebody presses Re-run.
    """

    def setUp(self):
        self._saved = dict(_registry)
        _registry.clear()
        from netbox_change_control.checks import register_builtin_checks

        register_builtin_checks(['has-changes'])
        self.requester = User.objects.create(username='attach-requester')
        self.policy = Policy.objects.create(name='Wants has-changes', checks=['has-changes'])

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved)

    def test_attaching_a_policy_runs_its_checks(self):
        cr = ChangeRequest.objects.create(
            branch=make_branch('attach', self._testMethodName),
            title='T',
            requester=self.requester,
        )
        self.assertFalse(cr.checks.exists(), 'no policy yet, so no check applies')

        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)

        check = cr.checks.get(name='has-changes')
        self.assertNotEqual(check.status, MergeCheckStatusChoices.PENDING)
        self.assertIsNotNone(check.completed)

    def test_detaching_the_policy_removes_its_checks(self):
        cr = ChangeRequest.objects.create(
            branch=make_branch('detach', self._testMethodName),
            title='T',
            requester=self.requester,
        )
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        self.assertTrue(cr.checks.filter(name='has-changes').exists())

        ChangeRequestPolicy.objects.filter(change_request=cr).delete()
        self.assertFalse(cr.checks.filter(name='has-changes').exists())
