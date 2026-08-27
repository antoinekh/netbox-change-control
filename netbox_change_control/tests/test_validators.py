"""
Tests for the merge gate.
"""

from django.test import TestCase, override_settings
from users.models import Group, User

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    MergeCheckStatusChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Review
from netbox_change_control.tests.base import make_policy
from netbox_change_control.validators import require_approved_change_request


class MergeGateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from netbox_branching.models import Branch

        cls.branch = Branch.objects.create(name='gated')
        cls.requester = User.objects.create(username='author')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer.groups.add(cls.group)

        cls.policy, cls.rule = make_policy(groups=[cls.group])

    def test_branch_without_change_request_is_blocked(self):
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('no change request', indicator.message.lower())

    def test_unapproved_change_request_is_blocked(self):
        ChangeRequest.objects.create(
            branch=self.branch,
            title='T',
            requester=self.requester,
            status=ChangeRequestStatusChoices.NEEDS_REVIEW,
        )
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)

    def test_approved_and_satisfied_change_request_is_permitted(self):
        cr = ChangeRequest.objects.create(
            branch=self.branch,
            title='T',
            requester=self.requester,
            status=ChangeRequestStatusChoices.APPROVED,
        )
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        Review.objects.create(change_request=cr, reviewer=self.reviewer, decision=ReviewDecisionChoices.APPROVE)

        # The gate has two independent conditions: the policies must be satisfied AND every
        # required check must pass. This test covers the review side, so the built-in checks
        # are marked passing. Check gating has its own tests in test_checks.py.
        cr.checks.update(status=MergeCheckStatusChoices.SUCCESS)

        indicator = require_approved_change_request(self.branch)
        self.assertTrue(indicator.permitted)

    def test_approved_status_alone_does_not_open_the_gate(self):
        """
        The gate re-evaluates. A request marked approved whose reviews no longer satisfy
        its policy must still be blocked.
        """
        cr = ChangeRequest.objects.create(
            branch=self.branch,
            title='T',
            requester=self.requester,
            status=ChangeRequestStatusChoices.APPROVED,
        )
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        # No reviews at all, yet status claims approved.
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)

    @override_settings(PLUGINS_CONFIG={'netbox_change_control': {'enforce_merge_gate': False}})
    def test_gate_can_be_disabled(self):
        indicator = require_approved_change_request(self.branch)
        self.assertTrue(indicator.permitted)
