"""
Policy conditions.

These pin the syntax the README documents, and that conditions read the state the branch
proposes rather than the state of main.
"""

from core.choices import ObjectChangeActionChoices
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.models import ChangeDiff
from users.models import User

from netbox_change_control.choices import ConditionStateChoices
from netbox_change_control.models import Policy
from netbox_change_control.policy import _conditions_match, match_policies
from netbox_change_control.tests.base import make_branch


class ConditionMatchingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='u')

    def setUp(self):
        self.branch = make_branch('cond', self._testMethodName)

    def _diff(self, action=ObjectChangeActionChoices.ACTION_UPDATE, **states):
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=1,
            object_repr='object',
            action=action,
        )
        # Written after creation: ChangeDiff.save() recomputes some fields.
        ChangeDiff.objects.filter(pk=diff.pk).update(**states)
        return diff

    def _matches(self, conditions):
        return _conditions_match(Policy(name='tmp', conditions=conditions), self.branch)

    def test_no_conditions_always_matches(self):
        self._diff(modified={'status': 'planned'})
        self.assertTrue(self._matches(None))

    def test_a_simple_equality(self):
        self._diff(modified={'status': 'active'})
        self.assertTrue(self._matches({'attr': 'status', 'value': 'active'}))
        self.assertFalse(self._matches({'attr': 'status', 'value': 'offline'}))

    def test_negation(self):
        self._diff(modified={'status': 'active'})
        self.assertFalse(self._matches({'attr': 'status', 'value': 'active', 'negate': True}))

    def test_an_and_ruleset(self):
        self._diff(modified={'status': 'active', 'tenant': 5})
        self.assertTrue(
            self._matches(
                {
                    'and': [
                        {'attr': 'status', 'value': 'active'},
                        {'attr': 'tenant', 'value': None, 'negate': True},
                    ]
                }
            )
        )

    def test_an_or_ruleset(self):
        self._diff(modified={'status': 'planned'})
        self.assertTrue(
            self._matches(
                {
                    'or': [
                        {'attr': 'status', 'value': 'active'},
                        {'attr': 'status', 'value': 'planned'},
                    ]
                }
            )
        )

    def test_the_regex_and_contains_operators(self):
        self._diff(modified={'cid': 'DEOW4921'})
        self.assertTrue(self._matches({'attr': 'cid', 'value': '^DEOW', 'op': 'regex'}))
        self.assertTrue(self._matches({'attr': 'cid', 'value': 'OW49', 'op': 'contains'}))

    def test_the_in_operator(self):
        self._diff(modified={'type': 2})
        self.assertTrue(self._matches({'attr': 'type', 'value': [1, 2], 'op': 'in'}))
        self.assertFalse(self._matches({'attr': 'type', 'value': [3, 4], 'op': 'in'}))

    def test_a_missing_attribute_does_not_match_and_does_not_raise(self):
        """
        A typo in `attr` must produce a policy that never applies, not a crash.
        """
        self._diff(modified={'status': 'active'})
        self.assertFalse(self._matches({'attr': 'stauts', 'value': 'active'}))

    def test_any_one_changed_object_is_enough(self):
        self._diff(modified={'status': 'planned'})
        self._diff(modified={'status': 'active'})
        self.assertTrue(self._matches({'attr': 'status', 'value': 'active'}))

    def test_the_branch_state_is_read_not_main(self):
        """
        `current` is main's state. Evaluating it would match a policy on somebody else's
        concurrent edit rather than on the change under review.
        """
        self._diff(modified={'status': 'planned'}, current={'status': 'active'})
        self.assertFalse(self._matches({'attr': 'status', 'value': 'active'}))
        self.assertTrue(self._matches({'attr': 'status', 'value': 'planned'}))

    def test_a_deletion_is_matched_on_what_is_removed(self):
        self._diff(
            action=ObjectChangeActionChoices.ACTION_DELETE,
            original={'status': 'active'},
            modified=None,
        )
        self.assertTrue(self._matches({'attr': 'status', 'value': 'active'}))


class ConditionStateTest(TestCase):
    """
    A change has two sides, and which one a condition reads decides what the policy protects.

    `status == active` read only against the state the branch leaves means "leaves it active".
    That misses a live circuit being decommissioned, which is the change most in need of a
    review, so the default reads both sides.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='state-requester')

    def _policy(self, condition_state):
        return Policy.objects.create(
            name=f'Active {condition_state}',
            conditions={'attr': 'status', 'value': 'active'},
            condition_state=condition_state,
        )

    def _diff(self, branch, before, after):
        """
        One changed object, with a status on each side of the change.
        """
        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        return ChangeDiff.objects.create(
            branch=branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=self._policy_target.pk,
            object_repr='thing',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
            original={'status': before},
            modified={'status': after},
            last_updated=None,
        )

    def setUp(self):
        self._policy_target = Policy.objects.create(name=f'target-{self._testMethodName}')
        self.branch = make_branch('state', self._testMethodName)

    def _matches(self, policy):
        return policy.name in [p.name for p, _ in match_policies(self.branch)]

    def test_the_default_catches_an_object_being_switched_off(self):
        """
        The case that motivated this: a live circuit being decommissioned.
        """
        self._diff(self.branch, before='active', after='decommissioned')
        self.assertTrue(self._matches(self._policy(ConditionStateChoices.EITHER)))

    def test_the_default_catches_an_object_being_switched_on(self):
        self._diff(self.branch, before='planned', after='active')
        self.assertTrue(self._matches(self._policy(ConditionStateChoices.EITHER)))

    def test_after_sees_only_the_state_the_branch_leaves(self):
        self._diff(self.branch, before='active', after='decommissioned')
        self.assertFalse(self._matches(self._policy(ConditionStateChoices.AFTER)))

    def test_after_still_catches_a_promotion(self):
        self._diff(self.branch, before='planned', after='active')
        self.assertTrue(self._matches(self._policy(ConditionStateChoices.AFTER)))

    def test_before_sees_only_the_state_being_replaced(self):
        self._diff(self.branch, before='planned', after='active')
        self.assertFalse(self._matches(self._policy(ConditionStateChoices.BEFORE)))

    def test_before_catches_an_object_being_switched_off(self):
        self._diff(self.branch, before='active', after='decommissioned')
        self.assertTrue(self._matches(self._policy(ConditionStateChoices.BEFORE)))

    def test_a_new_policy_defaults_to_either(self):
        self.assertEqual(Policy.objects.create(name='fresh').condition_state, ConditionStateChoices.EITHER)

    def test_main_is_never_consulted(self):
        """
        `current` describes main. Reading it would let a colleague's concurrent edit decide
        which policies govern this change request.
        """
        diff = self._diff(self.branch, before='planned', after='planned')
        type(diff).objects.filter(pk=diff.pk).update(current={'status': 'active'})
        self.assertFalse(self._matches(self._policy(ConditionStateChoices.EITHER)))


class SnapshotConditionTest(TestCase):
    """
    The `changed` and `unchanged` operators, and the `snapshots.` paths, which NetBox 4.7
    added. They answer the question a plain comparison cannot: not what a value is, but
    whether it moved.

    The plugin's part is only to hand the condition set both sides of the change under the
    key NetBox's event pipeline uses. Everything below therefore also pins that the two are
    wired together, because a condition written this way silently matches nothing if they
    are not.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='snapshot-u')

    def setUp(self):
        self.branch = make_branch('snap', self._testMethodName)

    def _diff(self, action=ObjectChangeActionChoices.ACTION_UPDATE, **states):
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=1,
            object_repr='object',
            action=action,
        )
        # Written after creation: ChangeDiff.save() recomputes some fields.
        ChangeDiff.objects.filter(pk=diff.pk).update(**states)
        return diff

    def _matches(self, conditions, condition_state=ConditionStateChoices.EITHER):
        policy = Policy(name='tmp', conditions=conditions, condition_state=condition_state)
        return _conditions_match(policy, self.branch)

    def test_changed_matches_a_value_that_moved(self):
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        self.assertTrue(self._matches({'attr': 'status', 'op': 'changed'}))

    def test_changed_does_not_match_a_value_that_stayed(self):
        self._diff(original={'status': 'active'}, modified={'status': 'active'})
        self.assertFalse(self._matches({'attr': 'status', 'op': 'changed'}))

    def test_unchanged_is_the_other_way_round(self):
        self._diff(original={'status': 'active'}, modified={'status': 'active'})
        self.assertTrue(self._matches({'attr': 'status', 'op': 'unchanged'}))
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        self.assertTrue(self._matches({'attr': 'status', 'op': 'changed'}))

    def test_a_snapshot_path_reads_one_named_side(self):
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        self.assertTrue(self._matches({'attr': 'snapshots.prechange.status', 'value': 'active'}))
        self.assertTrue(self._matches({'attr': 'snapshots.postchange.status', 'value': 'offline'}))
        self.assertFalse(self._matches({'attr': 'snapshots.prechange.status', 'value': 'offline'}))

    def test_the_direction_of_a_transition_can_be_pinned(self):
        """
        The case the two operators exist for: a live object being switched off, which must
        not also fire on one that was already off.
        """
        switched_off = {
            'and': [
                {'attr': 'status', 'op': 'changed'},
                {'attr': 'snapshots.prechange.status', 'value': 'active'},
            ]
        }
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        self.assertTrue(self._matches(switched_off))

    def test_an_object_already_off_is_not_a_transition(self):
        switched_off = {
            'and': [
                {'attr': 'status', 'op': 'changed'},
                {'attr': 'snapshots.prechange.status', 'value': 'active'},
            ]
        }
        self._diff(original={'status': 'offline'}, modified={'status': 'decommissioning'})
        self.assertFalse(self._matches(switched_off))

    def test_condition_state_does_not_narrow_a_snapshot_condition(self):
        """
        The setting picks which side plain attribute names read. These read both sides
        themselves, so all three settings have to agree.
        """
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        for state in (
            ConditionStateChoices.EITHER,
            ConditionStateChoices.AFTER,
            ConditionStateChoices.BEFORE,
        ):
            with self.subTest(condition_state=state):
                self.assertTrue(self._matches({'attr': 'status', 'op': 'changed'}, condition_state=state))

    def test_a_creation_counts_as_changed_on_every_condition_state(self):
        """
        A creation has no before, so `changed` is true: the attribute went from nothing to
        something. BEFORE is the case worth pinning, because it asks for a side this change
        does not have, and the condition still has to be evaluated.
        """
        self._diff(
            action=ObjectChangeActionChoices.ACTION_CREATE,
            original=None,
            modified={'status': 'active'},
        )
        for state in (
            ConditionStateChoices.EITHER,
            ConditionStateChoices.AFTER,
            ConditionStateChoices.BEFORE,
        ):
            with self.subTest(condition_state=state):
                self.assertTrue(self._matches({'attr': 'status', 'op': 'changed'}, condition_state=state))

    def test_a_deletion_counts_as_changed(self):
        self._diff(
            action=ObjectChangeActionChoices.ACTION_DELETE,
            original={'status': 'active'},
            modified=None,
        )
        self.assertTrue(self._matches({'attr': 'status', 'op': 'changed'}))

    def test_a_typo_in_a_snapshot_path_does_not_match_and_does_not_raise(self):
        """
        The same guarantee a plain attribute has: a policy that never applies, not a crash.
        """
        self._diff(original={'status': 'active'}, modified={'status': 'offline'})
        self.assertFalse(self._matches({'attr': 'stauts', 'op': 'changed'}))
        self.assertFalse(self._matches({'attr': 'snapshots.prechange.stauts', 'value': 'active'}))
