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

    def test_enabled(self):
        self.assertNotIn('Disabled one', self._filter({'enabled': True}))
        self.assertEqual(self._filter({'enabled': False}), {'Disabled one'})

    def test_weight(self):
        self.assertEqual(self._filter({'weight': [100]}), {'Enabled one'})

    def test_object_type_by_app_and_model(self):
        self.assertEqual(self._filter({'object_type': 'circuits.circuit'}), {'Circuits only'})

    def test_object_type_by_app_alone(self):
        self.assertEqual(self._filter({'object_type': 'circuits'}), {'Circuits only'})

    def test_has_conditions(self):
        self.assertEqual(self._filter({'has_conditions': True}), {'Conditional'})

    def test_search_covers_name_and_description(self):
        Policy.objects.create(name='Zulu', description='findme please')
        self.assertEqual(self._filter({'q': 'findme'}), {'Zulu'})


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

    def test_by_group(self):
        self.assertEqual(self._filter({'group_id': [self.engineers.pk]}), {'By group'})

    def test_by_named_user(self):
        self.assertEqual(self._filter({'user_id': [self.named.pk]}), {'By user'})

    def test_minimum_reviews(self):
        self.assertEqual(self._filter({'min_reviews': [2]}), {'By group'})

    def test_eligible_for_covers_groups_and_named_users(self):
        """
        A user satisfies a rule through a group or by being named, so both must match.
        """
        self.assertEqual(self._filter({'eligible_for': ['eng']}), {'By group'})
        self.assertEqual(self._filter({'eligible_for': ['named']}), {'By user'})


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

    def test_by_branch_name(self):
        self.assertEqual(self._filter({'branch': 'bare'}), {'Bare'})

    def test_by_applied_policy(self):
        self.assertEqual(self._filter({'policy_id': [self.policy.pk]}), {'Reviewed'})

    def test_by_reviewer(self):
        self.assertEqual(self._filter({'reviewer_id': [self.reviewer.pk]}), {'Reviewed'})

    def test_has_reviews(self):
        self.assertEqual(self._filter({'has_reviews': True}), {'Reviewed'})
        self.assertEqual(self._filter({'has_reviews': False}), {'Bare'})

    def test_auto_merge(self):
        self.assertEqual(self._filter({'auto_merge': True}), {'Reviewed'})

    def test_has_window(self):
        self.assertEqual(self._filter({'has_window': True}), {'Reviewed'})
        self.assertEqual(self._filter({'has_window': False}), {'Bare'})

    def test_branch_deleted(self):
        self.bare.branch.delete()
        self.assertEqual(self._filter({'branch_deleted': True}), {'Bare'})

    def test_search_covers_the_branch_name(self):
        """
        The branch name is a column, so the quick search should reach it.
        """
        self.assertEqual(self._filter({'q': 'bare-branch'}), {'Bare'})


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

    def test_by_status(self):
        self.assertEqual(self._filter({'status': ['success']}), {'ok'})

    def test_blocks_merge_excludes_advisory_checks(self):
        """
        An advisory check that failed does not block, so it must not appear.
        """
        self.assertEqual(self._filter({'blocks_merge': True}), {'bad'})

    def test_by_required(self):
        self.assertEqual(self._filter({'required': False}), {'advice'})


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

    def test_by_decision(self):
        self.assertEqual(self._filter({'decision': ['approve']}), {'approver'})

    def test_by_reviewer_username(self):
        self.assertEqual(self._filter({'reviewer': ['rejecter']}), {'rejecter'})


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

    def test_by_one_check(self):
        self.assertEqual(self._filter({'required_checks': ['no-conflicts']}), {'Everywhere'})

    def test_several_names_are_an_or(self):
        """
        The question is which policies bring a check in, not which require all of them.
        """
        self.assertEqual(
            self._filter({'required_checks': ['no-conflicts', 'threads-resolved']}),
            {'Everywhere', 'Devices'},
        )

    def test_an_externally_reported_name_filters_too(self):
        self.assertEqual(self._filter({'required_checks': ['cab-approval']}), {'External'})

    def test_a_name_nothing_requires_matches_nothing(self):
        self.assertEqual(self._filter({'required_checks': ['not-stale']}), set())

    def test_has_checks(self):
        self.assertEqual(self._filter({'has_checks': False}), {'No checks'})
        self.assertNotIn('No checks', self._filter({'has_checks': True}))
