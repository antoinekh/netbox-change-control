"""
Tests for policy matching and evaluation.

These exercise the engine directly, without provisioning a branch schema, so they run in
seconds.
"""

from django.test import TestCase
from users.models import Group, User

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, Review
from netbox_change_control.policy import evaluate_change_request
from netbox_change_control.tests.base import ChangeControlTestCase, add_rule, approve, make_branch, make_policy


class PolicyRuleEligibilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Engineers')
        cls.in_group = User.objects.create(username='in-group')
        cls.in_group.groups.add(cls.group)
        cls.named = User.objects.create(username='named')
        cls.outsider = User.objects.create(username='outsider')

        cls.policy, cls.rule = make_policy('Test policy', groups=[cls.group], users=[cls.named])

    def test_group_member_is_eligible(self):
        self.assertTrue(self.rule.is_eligible(self.in_group))

    def test_named_user_is_eligible(self):
        self.assertTrue(self.rule.is_eligible(self.named))

    def test_outsider_is_not_eligible(self):
        self.assertFalse(self.rule.is_eligible(self.outsider))

    def test_eligible_users_returns_union_without_duplicates(self):
        self.rule.users.add(self.in_group)
        pks = list(self.rule.eligible_users().values_list('pk', flat=True))
        self.assertCountEqual(pks, [self.in_group.pk, self.named.pk])


class EvaluationTest(TestCase):
    """
    Evaluation must count only approvals from users eligible for each rule.
    """

    @classmethod
    def setUpTestData(cls):
        cls.engineers = Group.objects.create(name='Engineers')
        cls.leads = Group.objects.create(name='Leads')

        cls.eng1 = User.objects.create(username='eng1')
        cls.eng2 = User.objects.create(username='eng2')
        cls.lead = User.objects.create(username='lead')
        cls.requester = User.objects.create(username='requester')
        for user in (cls.eng1, cls.eng2):
            user.groups.add(cls.engineers)
        cls.lead.groups.add(cls.leads)

        cls.policy, cls.eng_rule = make_policy(
            'Strict', groups=[cls.engineers], min_reviews=2, rule_name='Two engineers'
        )
        cls.lead_rule = add_rule(cls.policy, 'One lead', groups=[cls.leads])

    def _change_request(self):
        # A branch is required by the FK, but evaluation never reads it.
        branch = make_branch('eval', self._testMethodName)
        cr = ChangeRequest.objects.create(branch=branch, title='Test', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        return cr

    def _approve(self, cr, user):
        approve(cr, user)

    def test_unsatisfied_with_no_reviews(self):
        cr = self._change_request()
        evaluation = evaluate_change_request(cr)
        self.assertFalse(evaluation.satisfied)
        self.assertEqual(evaluation.outstanding, 3)

    def test_partial_approval_stays_unsatisfied(self):
        cr = self._change_request()
        self._approve(cr, self.eng1)
        self._approve(cr, self.eng2)
        evaluation = evaluate_change_request(cr)
        self.assertFalse(evaluation.satisfied)
        self.assertEqual(evaluation.outstanding, 1)

    def test_all_rules_met_is_satisfied(self):
        cr = self._change_request()
        self._approve(cr, self.eng1)
        self._approve(cr, self.eng2)
        self._approve(cr, self.lead)
        evaluation = evaluate_change_request(cr)
        self.assertTrue(evaluation.satisfied)

    def test_lead_approval_does_not_count_toward_engineer_rule(self):
        """
        A lead is not in the Engineers group, so their approval must not satisfy the
        engineer rule. This is the bug that makes a naive counter unsafe.
        """
        cr = self._change_request()
        self._approve(cr, self.lead)
        evaluation = evaluate_change_request(cr)
        by_name = {r.rule.name: r for r in evaluation.rules}
        self.assertEqual(by_name['Two engineers'].count, 0)
        self.assertEqual(by_name['One lead'].count, 1)

    def test_rejection_blocks_even_when_counts_are_met(self):
        cr = self._change_request()
        self._approve(cr, self.eng1)
        self._approve(cr, self.eng2)
        Review.objects.create(
            change_request=cr,
            reviewer=self.lead,
            decision=ReviewDecisionChoices.REJECT,
            comment='No',
        )
        evaluation = evaluate_change_request(cr)
        self.assertFalse(evaluation.satisfied)

    def test_request_with_no_rules_is_not_satisfied(self):
        """
        An unpoliced merge is what this plugin exists to prevent, so zero rules must not
        count as approval.
        """
        from netbox_branching.models import Branch

        branch = Branch.objects.create(name='no-policy')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester)
        self.assertFalse(evaluate_change_request(cr).satisfied)


class ReviewValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='author')
        cls.policy = Policy.objects.create(name='P')

    def test_author_cannot_review_own_request(self):
        from django.core.exceptions import ValidationError
        from netbox_branching.models import Branch

        branch = Branch.objects.create(name='self-review')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester)
        review = Review(change_request=cr, reviewer=self.requester, decision=ReviewDecisionChoices.APPROVE)
        with self.assertRaises(ValidationError):
            review.full_clean()

    def test_rejection_requires_a_comment(self):
        from django.core.exceptions import ValidationError
        from netbox_branching.models import Branch

        branch = Branch.objects.create(name='reject-no-comment')
        reviewer = User.objects.create(username='reviewer')
        cr = ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester)
        review = Review(change_request=cr, reviewer=reviewer, decision=ReviewDecisionChoices.REJECT)
        with self.assertRaises(ValidationError):
            review.full_clean()


class AutomaticApprovalTest(TestCase):
    """
    A rule needing zero approvals.

    Some changes should go through on their checks alone: a scripted, low-risk edit does not
    need a person to look at it, but it should still have to pass the same machine gate. A
    rule with `min_reviews = 0` says exactly that, and says it in the place a reader already
    looks rather than through a separate switch.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='automation')
        cls.policy, cls.rule = make_policy('Automatic', min_reviews=0, rule_name='No approval required')

    def _request(self):
        cr = ChangeRequest.objects.create(
            branch=make_branch('auto', self._testMethodName),
            title='Scripted change',
            requester=self.requester,
        )
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        cr.refresh_from_db()
        return cr

    def test_the_rule_is_satisfied_with_no_reviews(self):
        evaluation = evaluate_change_request(self._request())
        self.assertTrue(evaluation.satisfied)
        self.assertEqual(evaluation.outstanding, 0)

    def test_the_request_is_approved_without_anybody_acting(self):
        cr = self._request()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_the_checks_still_gate_the_merge(self):
        """
        The point of a zero rule is to drop the people gate, not the machine one.
        """
        from netbox_change_control.choices import MergeCheckStatusChoices
        from netbox_change_control.models import MergeCheck

        cr = self._request()
        MergeCheck.objects.create(
            change_request=cr,
            name='ci',
            label='CI',
            required=True,
            status=MergeCheckStatusChoices.FAILURE,
        )
        self.assertTrue(cr.is_approved)
        self.assertFalse(cr.is_ready_to_merge)
        self.assertIn('CI', cr.merge_blocked_reason)

    def test_a_rejection_still_blocks(self):
        """
        Nobody has to look, but anybody who does can stop it.
        """
        cr = self._request()
        reviewer = User.objects.create(username='passer-by')
        Review.objects.create(
            change_request=cr,
            reviewer=reviewer,
            decision=ReviewDecisionChoices.REJECT,
            comment='not this one',
        )
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.REJECTED)

    def test_it_does_not_weaken_another_policy(self):
        """
        Every rule of every attached policy must pass, so an automatic policy alongside a
        reviewed one leaves the reviewed one in force.
        """
        group = Group.objects.create(name='Engineers')
        engineer = User.objects.create(username='engineer')
        engineer.groups.add(group)
        strict, _rule = make_policy('Strict', groups=[group], rule_name='One engineer')

        cr = self._request()
        ChangeRequestPolicy.objects.create(change_request=cr, policy=strict)
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)

        approve(cr, engineer)
        cr.refresh_from_db()
        self.assertEqual(cr.status, ChangeRequestStatusChoices.APPROVED)


class ReasonsAudienceTest(ChangeControlTestCase):
    """
    The change request page lists every rule with its count, so repeating each shortfall in
    prose underneath said the same thing twice. The merge gate has no such table, so it still
    needs the full text.
    """

    branch_prefix = 'reasons'

    def test_a_plain_shortfall_adds_nothing_to_the_page(self):
        evaluation = self.cr.evaluate()
        self.assertEqual(evaluation.other_reasons(), [])
        self.assertTrue(any('needs 1 more' in r for r in evaluation.reasons()))

    def test_a_rejection_is_shown_because_no_rule_row_carries_it(self):
        Review.objects.create(
            change_request=self.cr,
            reviewer=self.reviewer,
            decision=ReviewDecisionChoices.REJECT,
            comment='not yet',
        )
        notes = self.cr.evaluate().other_reasons()
        self.assertEqual(len(notes), 1)
        self.assertIn('requested changes', notes[0])

    def test_having_no_rules_at_all_is_shown(self):
        ChangeRequestPolicy.objects.filter(change_request=self.cr).delete()
        self.cr.refresh_from_db()
        self.assertIn('No policy rules apply', ' '.join(self.cr.evaluate().other_reasons()))

    def test_the_merge_gate_still_names_every_unmet_rule(self):
        message = self.cr.merge_blocked_reason
        self.assertIn('not approved', message)
        self.assertTrue(any('needs' in r for r in self.cr.evaluate().reasons()))
