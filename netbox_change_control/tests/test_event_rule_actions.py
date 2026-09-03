"""
The event rule action type this plugin registers.

NetBox 4.7 lets a plugin add an entry to the Action type dropdown on an event rule.
`ReportCheckAction` uses it to close the direction the plugin could not go before: an
ordinary change in NetBox reporting a pre-merge check result back to the change request
reviewing it, written in the interface rather than in Python.

These cover the two halves separately. `validate()` is what an operator meets on the form, so
its job is to refuse a rule which could never work. `enqueue()` is what runs when the rule
fires, and its job is to write a result or write nothing, never to raise: an exception there
abandons every other rule NetBox was dispatching in the same batch.
"""

from typing import ClassVar

from core.events import OBJECT_UPDATED
from core.models import ObjectType
from dcim.models import Device, Site
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from extras.models import EventRule
from netbox.event_rules import get_event_rule_action
from netbox.registry import registry

from netbox_change_control.checks import sync_checks
from netbox_change_control.choices import MergeCheckStatusChoices
from netbox_change_control.event_rules import ReportCheckAction
from netbox_change_control.models import ChangeRequest, MergeCheck
from netbox_change_control.tests.base import ChangeControlTestCase

CHECK = 'safety-review'
SLUG = 'netbox_change_control.report_check'


class RegistrationTest(ChangeControlTestCase):
    policy_checks = ()

    def test_the_action_is_registered(self):
        """
        NetBox loads this from the plugin config. A module renamed or an export dropped leaves
        the dropdown without the entry, and nothing else fails.
        """
        self.assertIn(SLUG, registry['event_rule_actions'])
        self.assertIsInstance(get_event_rule_action(SLUG), ReportCheckAction)

    def test_it_is_marked_as_plugin_provided(self):
        """
        This is what makes NetBox isolate a failure here instead of letting it abort the other
        rules being dispatched alongside it.
        """
        self.assertTrue(get_event_rule_action(SLUG).is_plugin_provided)

    def test_it_takes_no_target_object(self):
        """
        The change request is found from the event, never picked on the rule. Declaring no
        object model is what removes the picker from the form and makes NetBox refuse a rule
        which supplies one anyway.
        """
        self.assertIsNone(get_event_rule_action(SLUG).object_model)


class ValidationTest(ChangeControlTestCase):
    policy_checks = ()

    def setUp(self):
        super().setUp()
        self.action = get_event_rule_action(SLUG)

    def _validate(self, action_data):
        self.action._validate(action_object=None, action_data=action_data)

    def test_a_minimal_configuration_is_accepted(self):
        self._validate({'check': CHECK})

    def test_a_full_configuration_is_accepted(self):
        self._validate(
            {
                'check': CHECK,
                'status': MergeCheckStatusChoices.SUCCESS,
                'summary': 'the pipeline is green',
                'details_url': 'https://ci.example.com/1',
            }
        )

    def test_the_check_name_is_required(self):
        """
        Without it the rule saves cleanly and then does nothing, silently, every time it
        fires. That is the failure worth catching on the form.
        """
        for action_data in (None, {}, {'status': MergeCheckStatusChoices.SUCCESS}, {'check': ''}):
            with self.subTest(action_data=action_data):
                with self.assertRaises(ValidationError):
                    self._validate(action_data)

    def test_action_data_must_be_an_object(self):
        with self.assertRaises(ValidationError):
            self._validate(['safety-review'])

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(ValidationError):
            self._validate({'check': CHECK, 'status': 'green'})

    def test_a_summary_must_be_text(self):
        with self.assertRaises(ValidationError):
            self._validate({'check': CHECK, 'summary': 12})

    def test_a_details_url_must_be_text(self):
        with self.assertRaises(ValidationError):
            self._validate({'check': CHECK, 'details_url': 12})

    def test_a_details_url_longer_than_the_column_is_refused(self):
        """
        The column is 200 characters. A longer one used to reach `row.save()` and raise a
        DataError from inside the event pipeline, which is the one thing an action must never
        do. Refusing it on the form is where the operator can still fix it.
        """
        limit = MergeCheck._meta.get_field('details_url').max_length
        self._validate({'check': CHECK, 'details_url': 'https://e.com/' + 'a' * (limit - 14)})
        with self.assertRaises(ValidationError):
            self._validate({'check': CHECK, 'details_url': 'https://e.com/' + 'a' * limit})

    def test_an_object_cannot_be_attached(self):
        """
        The base class enforces this from `object_model = None`, and it is worth pinning: it
        is the difference between the form offering a picker and not.
        """
        with self.assertRaises(ValidationError):
            self.action._validate(action_object=Site(name='x'), action_data={'check': CHECK})

    def test_the_rule_form_refuses_a_bad_configuration(self):
        """
        The same validation, reached the way an operator reaches it.
        """
        rule = EventRule(
            name='Flag it',
            event_types=[OBJECT_UPDATED],
            action_type=SLUG,
            action_data={'status': MergeCheckStatusChoices.FAILURE},
        )
        with self.assertRaises(ValidationError):
            rule.full_clean(exclude=['object_types'])


class ReportingTest(ChangeControlTestCase):
    """
    `enqueue()` against a change request whose branch the event came from.
    """

    policy_checks = ()

    def setUp(self):
        super().setUp()
        self.require_checks(CHECK)
        sync_checks(self.cr)
        self.action = get_event_rule_action(SLUG)
        self.rule = EventRule(name='Flag it', action_type=SLUG, action_data={'check': CHECK})

    def _context(self, **overrides):
        """
        What NetBox hands an action: the event, with the request it came from.

        The branch is read off the request rather than out of branching's contextvar, because
        the contextvar is already unset by the time events are flushed. A request carrying the
        attribute is therefore what a real dispatch looks like.
        """
        request = RequestFactory().post('/')
        request.active_branch = self.branch
        context = {
            'event_type': OBJECT_UPDATED,
            'object_type': ObjectType.objects.get_for_model(Site),
            'request': request,
            'data': {'display': 'site-1'},
        }
        context.update(overrides)
        return context

    def _fire(self, context=None, rule=None):
        self.action.enqueue(
            event_rule=rule or self.rule,
            event_context=context or self._context(),
            action_object=None,
            action_data={},
        )
        return MergeCheck.objects.get(change_request=self.cr, name=CHECK)

    def test_it_records_a_result_on_the_branch_change_request(self):
        """
        The point of the whole action: a change to an ordinary object, made in a branch, moves
        a check on the change request reviewing that branch.
        """
        row = self._fire()
        self.assertEqual(row.status, MergeCheckStatusChoices.FAILURE)
        self.assertIsNotNone(row.completed)

    def test_a_failure_blocks_the_merge(self):
        """
        The reason an operator writes one of these.
        """
        self.assertTrue(self._fire().blocks_merge)

    def test_the_status_defaults_to_failure(self):
        """
        A rule exists to flag something, so the useful default is the blocking one. Reporting
        a pass has to be asked for.
        """
        self.assertEqual(self._fire().status, MergeCheckStatusChoices.FAILURE)

    def test_a_configured_status_is_used(self):
        rule = EventRule(
            name='Green',
            action_type=SLUG,
            action_data={'check': CHECK, 'status': MergeCheckStatusChoices.SUCCESS},
        )
        row = self._fire(rule=rule)
        self.assertEqual(row.status, MergeCheckStatusChoices.SUCCESS)
        self.assertFalse(row.blocks_merge)

    def test_a_configured_summary_and_url_are_stored(self):
        rule = EventRule(
            name='Detailed',
            action_type=SLUG,
            action_data={
                'check': CHECK,
                'summary': 'a live circuit is being switched off',
                'details_url': 'https://ci.example.com/7',
            },
        )
        row = self._fire(rule=rule)
        self.assertEqual(row.summary, 'a live circuit is being switched off')
        self.assertEqual(row.details_url, 'https://ci.example.com/7')

    def test_the_default_summary_names_the_rule_and_the_object(self):
        """
        A red badge with no explanation, in the one place somebody decides whether to allow a
        change, is worse than useless. The default has to say what tripped it.
        """
        summary = self._fire().summary
        self.assertIn('Flag it', summary)
        self.assertIn('site-1', summary)

    def test_a_long_summary_is_cut_rather_than_refused(self):
        rule = EventRule(name='Verbose', action_type=SLUG, action_data={'check': CHECK, 'summary': 'x' * 900})
        self.assertEqual(len(self._fire(rule=rule).summary), MergeCheck._meta.get_field('summary').max_length)

    def test_a_long_details_url_is_dropped_rather_than_cut(self):
        """
        `validate()` refuses this on the form, so a rule can only carry one if it was written
        straight to the database. The result still has to be reported: an action which raised
        here would abandon whatever else NetBox was dispatching alongside it.

        The link goes, not the result. A cut URL points somewhere else, or nowhere.
        """
        limit = MergeCheck._meta.get_field('details_url').max_length
        rule = EventRule(
            name='Long link',
            action_type=SLUG,
            action_data={'check': CHECK, 'details_url': 'https://e.com/' + 'a' * limit},
        )
        row = self._fire(rule=rule)
        self.assertEqual(row.details_url, '')
        self.assertEqual(row.status, MergeCheckStatusChoices.FAILURE)

    def test_an_event_about_a_change_request_names_it_directly(self):
        """
        A rule on this plugin's own lifecycle events carries the change request as the event's
        object, so there is no branch to go through.
        """
        context = self._context(object=self.cr)
        context.pop('request')
        row = self._fire(context=context)
        self.assertEqual(row.status, MergeCheckStatusChoices.FAILURE)

    def test_repeating_the_same_answer_writes_nothing(self):
        """
        A rule which fires twice on an unchanged answer is not a change. Recording it would
        fill the changelog with noise and bury the transitions worth reading.
        """
        first = self._fire()
        stamp = first.last_updated
        second = self._fire()
        self.assertEqual(second.last_updated, stamp)

    def test_a_changed_answer_is_written(self):
        self._fire()
        rule = EventRule(
            name='Green',
            action_type=SLUG,
            action_data={'check': CHECK, 'status': MergeCheckStatusChoices.SUCCESS},
        )
        self.assertEqual(self._fire(rule=rule).status, MergeCheckStatusChoices.SUCCESS)


class NothingToReportTest(ChangeControlTestCase):
    """
    Every way the action can find nothing to do. None of them may raise: the base class is
    explicit that a rule's own configuration problem must not abandon the batch.
    """

    policy_checks = ()

    def setUp(self):
        super().setUp()
        self.action = get_event_rule_action(SLUG)
        self.rule = EventRule(name='Flag it', action_type=SLUG, action_data={'check': CHECK})

    def _fire(self, context):
        self.action.enqueue(
            event_rule=self.rule,
            event_context=context,
            action_object=None,
            action_data={},
        )

    def test_an_event_on_main_is_ignored(self):
        """
        The ordinary case, not a fault. An event rule fires for changes made on main as well
        as in a branch, and a change on main has no change request to report to.
        """
        request = RequestFactory().post('/')
        request.active_branch = None
        self._fire({'event_type': OBJECT_UPDATED, 'request': request, 'data': {}})
        self.assertFalse(MergeCheck.objects.filter(change_request=self.cr, name=CHECK).exists())

    def test_an_event_with_no_request_at_all_is_ignored(self):
        self._fire({'event_type': OBJECT_UPDATED, 'data': {}})
        self.assertFalse(MergeCheck.objects.filter(change_request=self.cr, name=CHECK).exists())

    def test_a_branch_with_no_change_request_is_ignored(self):
        from netbox_change_control.tests.base import make_branch

        request = RequestFactory().post('/')
        request.active_branch = make_branch('orphan', self._testMethodName)
        self._fire({'event_type': OBJECT_UPDATED, 'request': request, 'data': {}})
        self.assertFalse(MergeCheck.objects.filter(name=CHECK).exists())

    def test_an_undeclared_check_is_not_invented(self):
        """
        `sync_checks` deletes any row the configuration does not expect, so a check conjured
        here would vanish at the next evaluation and take its blocking result with it. The
        name has to be declared, exactly as for a result reported over the REST API.
        """
        request = RequestFactory().post('/')
        request.active_branch = self.branch
        self._fire({'event_type': OBJECT_UPDATED, 'request': request, 'data': {}})
        self.assertFalse(MergeCheck.objects.filter(change_request=self.cr, name=CHECK).exists())

    def test_a_rule_with_no_check_name_is_ignored(self):
        """
        Validation stops this on the form, but a rule saved before the action was installed,
        or edited over the API, can still reach here.
        """
        self.rule.action_data = {}
        request = RequestFactory().post('/')
        request.active_branch = self.branch
        self._fire({'event_type': OBJECT_UPDATED, 'request': request, 'data': {}})
        self.assertFalse(MergeCheck.objects.filter(change_request=self.cr).exists())


class DispatchTest(ChangeControlTestCase):
    """
    The action reached through NetBox's own event pipeline rather than called directly, so the
    wiring between the two is covered as well as the action itself.
    """

    policy_checks = ()
    # The two gates are independent and the people gate speaks first, so a request nobody has
    # approved reports that rather than the check. Approving it is what leaves the check as
    # the only thing standing between this branch and a merge, which is what these assert.
    approved = True

    def setUp(self):
        super().setUp()
        self.require_checks(CHECK)
        sync_checks(self.cr)

    def test_a_matching_rule_reports_through_the_pipeline(self):
        from extras.events import process_event_rules

        site = Site.objects.create(name='Site 1', slug='site-1')
        object_type = ObjectType.objects.get_for_model(Site)

        rule = EventRule.objects.create(
            name='Flag a rename',
            event_types=[OBJECT_UPDATED],
            action_type=SLUG,
            action_data={'check': CHECK, 'summary': 'a site was renamed in this branch'},
        )
        rule.object_types.set([object_type])

        request = RequestFactory().post('/')
        request.active_branch = self.branch

        process_event_rules(
            event_rules=[rule],
            object_type=object_type,
            event={
                'event_type': OBJECT_UPDATED,
                'object_type': object_type,
                'object': site,
                'request': request,
                'data': {'id': site.pk, 'display': str(site)},
                'snapshots': {'prechange': {'name': 'Site 0'}, 'postchange': {'name': 'Site 1'}},
            },
        )

        row = MergeCheck.objects.get(change_request=self.cr, name=CHECK)
        self.assertEqual(row.status, MergeCheckStatusChoices.FAILURE)
        self.assertEqual(row.summary, 'a site was renamed in this branch')

    def test_the_change_request_is_no_longer_ready_to_merge(self):
        """
        The result has to reach the gate, not just the row. The cached readiness the list
        reads is refreshed by the check's own receiver, which is why the action saves the row
        rather than updating it in place.
        """
        from netbox_change_control.event_rules import ReportCheckAction

        request = RequestFactory().post('/')
        request.active_branch = self.branch
        ReportCheckAction().enqueue(
            event_rule=EventRule(name='Flag it', action_type=SLUG, action_data={'check': CHECK}),
            event_context={'event_type': OBJECT_UPDATED, 'request': request, 'data': {}},
            action_object=None,
            action_data={},
        )

        self.cr.refresh_from_db()
        self.assertFalse(self.cr.cached_ready_to_merge)
        self.assertIn(CHECK, ChangeRequest.objects.get(pk=self.cr.pk).merge_blocked_reason)


class DocumentedExampleTest(ChangeControlTestCase):
    """
    The pair of rules the documentation walks through, run exactly as written.

    Two rules on one check are what make a check usable rather than a one-way trapdoor: the
    first fails it, the second is how it goes green again. A worked example which only ever
    fails would leave a reader with a branch they cannot merge and no way out.

    The conditions are copied from docs/event-rules.md, character for character. That is the
    point of the test: somebody will paste that JSON into a real deployment, so it has to be
    the JSON that works, not the JSON that reads well.
    """

    policy_checks = ()
    approved = True

    CHECK = 'device-tenancy'

    # A device left active with no tenant fails the check.
    FAILING: ClassVar = {
        'and': [
            {'attr': 'status.value', 'value': 'active'},
            {'attr': 'tenant', 'value': None},
        ]
    }
    # The same device, once somebody assigns a tenant, passes it.
    PASSING: ClassVar = {
        'and': [
            {'attr': 'status.value', 'value': 'active'},
            {'attr': 'tenant', 'value': None, 'negate': True},
        ]
    }

    def setUp(self):
        super().setUp()
        self.require_checks(self.CHECK)
        sync_checks(self.cr)

    def _rule(self, name, conditions, status, summary):
        rule = EventRule.objects.create(
            name=name,
            event_types=[OBJECT_UPDATED],
            conditions=conditions,
            action_type=SLUG,
            action_data={'check': self.CHECK, 'status': status, 'summary': summary},
        )
        rule.object_types.set([ObjectType.objects.get_for_model(Device)])
        rule.full_clean(exclude=['object_types'])
        return rule

    def _fire(self, rules, tenant):
        """
        One device update, put through NetBox's pipeline with both rules watching it.

        The payload is the REST serialization, which is what an event rule condition reads, so
        `status` is an object and the condition has to say `status.value`. The snapshots beside
        it hold raw field values, where the same field is a bare string. Both shapes appear in
        the documented example, and this is where that is checked rather than assumed.
        """
        from extras.events import process_event_rules

        object_type = ObjectType.objects.get_for_model(Device)
        request = RequestFactory().post('/')
        request.active_branch = self.branch

        process_event_rules(
            event_rules=rules,
            object_type=object_type,
            event={
                'event_type': OBJECT_UPDATED,
                'object_type': object_type,
                'request': request,
                'data': {
                    'id': 1,
                    'display': 'ncake101',
                    'name': 'ncake101',
                    'status': {'value': 'active', 'label': 'Active'},
                    'tenant': tenant,
                },
                'snapshots': {
                    'prechange': {'status': 'planned', 'tenant': None},
                    'postchange': {'status': 'active', 'tenant': tenant['id'] if tenant else None},
                },
            },
        )
        return MergeCheck.objects.get(change_request=self.cr, name=self.CHECK)

    def _rules(self):
        return [
            self._rule('Device active without a tenant', self.FAILING, 'failure', 'active with no tenant assigned'),
            self._rule('Device active with a tenant', self.PASSING, 'success', 'tenant assigned'),
        ]

    def test_active_without_a_tenant_fails_the_check(self):
        row = self._fire(self._rules(), tenant=None)
        self.assertEqual(row.status, MergeCheckStatusChoices.FAILURE)
        self.assertEqual(row.summary, 'active with no tenant assigned')
        self.assertTrue(row.blocks_merge)

    def test_active_with_a_tenant_passes_it(self):
        row = self._fire(self._rules(), tenant={'id': 3, 'name': 'Acme'})
        self.assertEqual(row.status, MergeCheckStatusChoices.SUCCESS)
        self.assertFalse(row.blocks_merge)

    def test_assigning_a_tenant_clears_a_failure(self):
        """
        The half a one-rule example cannot show. The check has to be able to go green again,
        or the first failure is a dead end.
        """
        rules = self._rules()
        self.assertEqual(self._fire(rules, tenant=None).status, MergeCheckStatusChoices.FAILURE)
        self.assertEqual(
            self._fire(rules, tenant={'id': 3, 'name': 'Acme'}).status,
            MergeCheckStatusChoices.SUCCESS,
        )

    def test_the_two_rules_never_both_match(self):
        """
        They are written as each other's negation on purpose. If both could match, the result
        would depend on which rule NetBox happened to dispatch last.
        """
        from extras.conditions import ConditionSet

        for tenant in (None, {'id': 3, 'name': 'Acme'}):
            data = {'status': {'value': 'active', 'label': 'Active'}, 'tenant': tenant}
            with self.subTest(tenant=tenant):
                matched = [ConditionSet(conditions).eval(data) for conditions in (self.FAILING, self.PASSING)]
                self.assertEqual(matched.count(True), 1, 'exactly one rule must match')

    def test_a_device_which_is_not_active_matches_neither(self):
        """
        The check is about devices being put into service, so a planned device with no tenant
        is not a finding and must not be reported as one.
        """
        from extras.conditions import ConditionSet

        data = {'status': {'value': 'planned', 'label': 'Planned'}, 'tenant': None}
        for conditions in (self.FAILING, self.PASSING):
            self.assertFalse(ConditionSet(conditions).eval(data))
