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

from netbox_change_control.models import Policy
from netbox_change_control.policy import _conditions_match
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
