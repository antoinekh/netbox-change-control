"""
Filters.

Every column a table can display should be filterable, so a user who can see a value can
narrow by it. These check that each filter actually restricts the queryset, not merely that
the page returns 200.
"""

from django.test import TestCase
from netbox_branching.models import Branch
from users.models import Group, User

from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.filtersets import (
    ChangeRequestFilterSet,
    MergeCheckFilterSet,
    PolicyFilterSet,
    PolicyRuleFilterSet,
    ReviewFilterSet,
)
from netbox_change_control.models import (
    ChangeRequest,
    ChangeRequestPolicy,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.tests.base import add_rule, make_policy


class PolicyFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import ObjectType

        cls.enabled = Policy.objects.create(name='Enabled one', weight=100)
        cls.disabled = Policy.objects.create(name='Disabled one', enabled=False, weight=200)
        cls.scoped = Policy.objects.create(name='Circuits only', weight=300)
        cls.scoped.object_types.set(ObjectType.objects.filter(app_label='circuits', model='circuit'))
        cls.conditional = Policy.objects.create(name='Conditional', conditions={'attr': 'status', 'value': 'active'})

    def _filter(self, data):
        return set(PolicyFilterSet(data, queryset=Policy.objects.all()).qs.values_list('name', flat=True))

    def test_filters(self):
        Policy.objects.create(name='Zulu', description='findme please')
        cases = (
            ({'enabled': False}, {'Disabled one'}),
            ({'weight': [100]}, {'Enabled one'}),
            ({'object_type': 'circuits.circuit'}, {'Circuits only'}),
            ({'object_type': 'circuits'}, {'Circuits only'}),
            ({'has_conditions': True}, {'Conditional'}),
            # The search reaches the description too.
            ({'q': 'findme'}, {'Zulu'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)


class PolicyRuleFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.engineers = Group.objects.create(name='Engineers')
        cls.leads = Group.objects.create(name='Leads')
        cls.eng = User.objects.create(username='eng')
        cls.eng.groups.add(cls.engineers)
        cls.named = User.objects.create(username='named')

        cls.policy, cls.by_group = make_policy('P', groups=[cls.engineers], min_reviews=2, rule_name='By group')
        cls.by_user = add_rule(cls.policy, 'By user', users=[cls.named])
        cls.other = add_rule(cls.policy, 'Leads only', groups=[cls.leads])

    def _filter(self, data):
        return set(PolicyRuleFilterSet(data, queryset=PolicyRule.objects.all()).qs.values_list('name', flat=True))

    def test_filters(self):
        cases = (
            ({'group_id': [self.engineers.pk]}, {'By group'}),
            ({'user_id': [self.named.pk]}, {'By user'}),
            ({'min_reviews': [2]}, {'By group'}),
            # A user satisfies a rule through a group or by being named, so both match.
            ({'eligible_for': ['eng']}, {'By group'}),
            ({'eligible_for': ['named']}, {'By user'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)


class ChangeRequestFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.utils import timezone

        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.requester = User.objects.create(username='requester')

        cls.policy = Policy.objects.create(name='Applied')
        rule = PolicyRule.objects.create(policy=cls.policy, name='R', min_reviews=1)
        rule.groups.add(cls.group)

        cls.reviewed = ChangeRequest.objects.create(
            branch=Branch.objects.create(name='reviewed-branch'),
            title='Reviewed',
            requester=cls.requester,
            auto_merge=True,
            scheduled_start=timezone.now(),
        )
        ChangeRequestPolicy.objects.create(change_request=cls.reviewed, policy=cls.policy)
        Review.objects.create(
            change_request=cls.reviewed,
            reviewer=cls.reviewer,
            decision=ReviewDecisionChoices.APPROVE,
        )

        cls.bare = ChangeRequest.objects.create(
            branch=Branch.objects.create(name='bare-branch'),
            title='Bare',
            requester=cls.requester,
        )

    def _filter(self, data):
        return set(
            ChangeRequestFilterSet(data, queryset=ChangeRequest.objects.all()).qs.values_list('title', flat=True)
        )

    def test_filters(self):
        cases = (
            ({'branch': 'bare'}, {'Bare'}),
            ({'policy_id': [self.policy.pk]}, {'Reviewed'}),
            ({'reviewer_id': [self.reviewer.pk]}, {'Reviewed'}),
            ({'has_reviews': True}, {'Reviewed'}),
            ({'has_reviews': False}, {'Bare'}),
            ({'auto_merge': True}, {'Reviewed'}),
            ({'has_window': True}, {'Reviewed'}),
            ({'has_window': False}, {'Bare'}),
            # The branch name is a column, so the quick search reaches it.
            ({'q': 'bare-branch'}, {'Bare'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)

    def test_branch_deleted(self):
        self.bare.branch.delete()
        self.assertEqual(self._filter({'branch_deleted': True}), {'Bare'})


class MergeCheckFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.cr = ChangeRequest.objects.create(
            branch=Branch.objects.create(name='checked'),
            title='T',
            requester=cls.requester,
        )
        cls.cr.checks.all().delete()
        cls.passing = MergeCheck.objects.create(
            change_request=cls.cr,
            name='ok',
            label='OK',
            status=MergeCheckStatusChoices.SUCCESS,
        )
        cls.failing = MergeCheck.objects.create(
            change_request=cls.cr,
            name='bad',
            label='Bad',
            status=MergeCheckStatusChoices.FAILURE,
        )
        cls.advisory = MergeCheck.objects.create(
            change_request=cls.cr,
            name='advice',
            label='Advice',
            required=False,
            status=MergeCheckStatusChoices.FAILURE,
        )

    def _filter(self, data):
        return set(MergeCheckFilterSet(data, queryset=MergeCheck.objects.all()).qs.values_list('name', flat=True))

    def test_filters(self):
        cases = (
            ({'status': ['success']}, {'ok'}),
            # An advisory check that failed does not block.
            ({'blocks_merge': True}, {'bad'}),
            ({'required': False}, {'advice'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)


class ReviewFilterTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.approver = User.objects.create(username='approver')
        cls.rejecter = User.objects.create(username='rejecter')
        cls.cr = ChangeRequest.objects.create(
            branch=Branch.objects.create(name='reviewed'),
            title='T',
            requester=cls.requester,
        )
        Review.objects.create(change_request=cls.cr, reviewer=cls.approver, decision=ReviewDecisionChoices.APPROVE)
        Review.objects.create(
            change_request=cls.cr,
            reviewer=cls.rejecter,
            decision=ReviewDecisionChoices.REJECT,
            comment='no',
        )

    def _filter(self, data):
        return set(ReviewFilterSet(data, queryset=Review.objects.all()).qs.values_list('reviewer__username', flat=True))

    def test_filters(self):
        cases = (
            ({'decision': ['approve']}, {'approver'}),
            ({'reviewer': ['rejecter']}, {'rejecter'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)


class PolicyCheckFilterTest(TestCase):
    """
    Which policies bring a given check in. With built-ins chosen per policy, this is how you
    answer "does anything still require no-conflicts?".
    """

    @classmethod
    def setUpTestData(cls):
        Policy.objects.create(name='Everywhere', checks=['has-changes', 'no-conflicts'])
        Policy.objects.create(name='Devices', checks=['threads-resolved'])
        Policy.objects.create(name='External', checks=['cab-approval'])
        Policy.objects.create(name='No checks')

    def _filter(self, data):
        return set(PolicyFilterSet(data, queryset=Policy.objects.all()).qs.values_list('name', flat=True))

    def test_filters(self):
        cases = (
            ({'required_checks': ['no-conflicts']}, {'Everywhere'}),
            # The question is which policies bring a check in, so several names are an or.
            ({'required_checks': ['no-conflicts', 'threads-resolved']}, {'Everywhere', 'Devices'}),
            ({'required_checks': ['cab-approval']}, {'External'}),
            ({'required_checks': ['not-stale']}, set()),
            ({'has_checks': False}, {'No checks'}),
            ({'has_checks': True}, {'Everywhere', 'Devices', 'External'}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)


class ChangeRequestRefTest(TestCase):
    """
    `ref` carries whatever a team already calls a change: a ticket id, a change number.
    It has to be filterable and searchable, or it is just decoration.
    """

    @classmethod
    def setUpTestData(cls):
        requester = User.objects.create(username='ref-requester')
        for name, ref in (('a', 'CHG0012345'), ('b', 'INC-99'), ('c', '')):
            ChangeRequest.objects.create(
                branch=Branch.objects.create(name=f'ref-{name}'),
                title=f'Request {name}',
                ref=ref,
                requester=requester,
            )

    def _filter(self, data):
        return set(ChangeRequestFilterSet(data, queryset=ChangeRequest.objects.all()).qs.values_list('ref', flat=True))

    def test_filters(self):
        cases = (
            ({'ref': ['CHG0012345']}, {'CHG0012345'}),
            ({'ref': ['chg']}, {'CHG0012345'}),
            ({'ref': ['CHG0012345', 'INC-99']}, {'CHG0012345', 'INC-99'}),
            ({'q': 'INC-99'}, {'INC-99'}),
            # The reference is optional.
            ({}, {'CHG0012345', 'INC-99', ''}),
        )
        for data, expected in cases:
            with self.subTest(data=data):
                self.assertEqual(self._filter(data), expected)
