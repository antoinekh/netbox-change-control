"""
Shared fixtures for the test suite.

Nothing here declares a test. A class which carries its own tests must never be subclassed
for its fixture: Python re-runs every one of those tests under the subclass, which inflates
the count without covering anything new.
"""

from django.test import TestCase
from netbox_branching.models import Branch
from users.models import Group, User

from netbox_change_control.checks import BUILTIN_CHECKS
from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule, Review

__all__ = (
    'ChangeControlTestCase',
    'add_rule',
    'approve',
    'make_branch',
    'make_policy',
    'pass_checks',
    'submit',
)


def make_policy(name='One review', *, groups=(), users=(), min_reviews=1, rule_name='Rule', checks=(), **kwargs):
    """
    A policy with a single rule. Returns both, because a test usually needs to reach the rule.
    """
    policy = Policy.objects.create(name=name, checks=list(checks), **kwargs)
    rule = add_rule(policy, rule_name, groups=groups, users=users, min_reviews=min_reviews)
    return policy, rule


def add_rule(policy, name, *, groups=(), users=(), min_reviews=1):
    rule = PolicyRule.objects.create(policy=policy, name=name, min_reviews=min_reviews)
    rule.groups.set(groups)
    rule.users.set(users)
    return rule


def make_branch(prefix, suffix):
    """
    A branch named for the test that owns it. Branch names are unique and capped at 100.
    """
    return Branch.objects.create(name=f'{prefix}-{suffix}'[:100])


def submit(change_request):
    """
    Put a change request into review, as its author would.

    Draft is the author's to hold, so a review submitted against a draft moves nothing. A test
    that is about reviewing has to get past this first.
    """
    change_request.submit()
    change_request.refresh_from_db()
    return change_request


def approve(change_request, reviewer, comment=''):
    return Review.objects.create(
        change_request=change_request,
        reviewer=reviewer,
        decision=ReviewDecisionChoices.APPROVE,
        comment=comment,
    )


def pass_checks(change_request):
    """
    Clear the check gate, so a test about one gate is not blocked by another.
    """
    change_request.checks.update(status=MergeCheckStatusChoices.SUCCESS)


class ChangeControlTestCase(TestCase):
    """
    The fixture nearly every test needs: one reviewer group, a requester, and a policy
    requiring a single review from that group.

    Each test gets its own branch and a change request with the policy applied. Set
    `approved = True` for the review to be given already, or `submitted = False` to leave the
    request as a draft.
    """

    branch_prefix = 'cc'
    approved = False
    #: Whether the request has been submitted for review.
    #:
    #: Draft is the author's to hold, so the evaluation does not move a request out of it and
    #: a review submitted against a draft changes nothing. Nearly every test is about what
    #: happens after submission, which is also the only state a reviewer ever sees.
    submitted = True
    #: Checks the fixture policy requires. A built-in is registered but never applied until a
    #: policy names it, so the default mirrors the catch-all policy a real deployment uses to
    #: get the built-ins everywhere. Set it to () where the registry is cleared.
    policy_checks = None

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Engineers')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.requester = User.objects.create(username='requester')
        checks = list(BUILTIN_CHECKS) if cls.policy_checks is None else list(cls.policy_checks)
        cls.policy, cls.rule = make_policy(groups=[cls.group], checks=checks)

    def setUp(self):
        self.branch = make_branch(self.branch_prefix, self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)
        if self.submitted:
            self.cr.submit()
        if self.approved:
            approve(self.cr, self.reviewer)
        self.cr.refresh_from_db()

    def _approve(self, user=None, comment=''):
        return approve(self.cr, user or self.reviewer, comment)

    def require_checks(self, *names):
        """
        Have the fixture policy require these checks, which is what makes them apply.
        """
        self.policy.checks = sorted(set(self.policy.checks or []) | set(names))
        self.policy.save(update_fields=['checks'])
