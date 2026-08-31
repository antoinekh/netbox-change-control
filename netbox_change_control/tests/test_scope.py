"""
Tests for the policy scope following the branch.

Which policies govern a change request is decided from the object types its branch touches.
A branch is not fixed at submission time: an author can keep editing inside it, and each edit
can bring in an object type no attached policy covers.

If the governing set does not follow, the gate can be walked around. Open a request on a
branch touching only low-risk objects, collect the light approval that attracts, then add the
real change to the same branch. The approvals go stale and the status returns to Needs review,
but a policy that never attached asks for nothing, so the same reviewer approves a second time
and the work merges unseen by anybody with the authority to judge it.

These tests pin both halves: the branch growing an object type must pull the policy in, and
the branch losing one must let it go.
"""

from core.models import ObjectType
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.models import ChangeDiff
from users.models import Group, User

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.policy import sync_policies
from netbox_change_control.tests.base import approve, make_branch
from netbox_change_control.validators import require_approved_change_request


def touch(branch, model, object_id, repr_='thing', **kwargs):
    """
    Record that the branch changes one object, the way branching does.

    ChangeDiff rows are always written one at a time by branching's own receiver, never in
    bulk, so creating them here exercises the same post_save the plugin listens on.
    """
    return ChangeDiff.objects.create(
        branch=branch,
        action=kwargs.pop('action', 'update'),
        object_type=ContentType.objects.get_for_model(model),
        object_id=object_id,
        object_repr=repr_,
        original=kwargs.pop('original', {'status': 'active'}),
        modified=kwargs.pop('modified', {'status': 'active'}),
        current=kwargs.pop('current', {'status': 'active'}),
    )


class ScopeFollowsTheBranchTest(TestCase):
    """
    The light policy covers prefixes and asks for one engineer. The heavy policy covers
    devices and asks for a lead as well. A branch that grows a device must pick up the heavy
    one, whenever that growth happens.
    """

    @classmethod
    def setUpTestData(cls):
        from dcim.models import Device
        from ipam.models import Prefix

        cls.engineers = Group.objects.create(name='Engineers')
        cls.leads = Group.objects.create(name='Leads')
        cls.engineer = User.objects.create(username='engineer')
        cls.engineer.groups.add(cls.engineers)
        cls.lead = User.objects.create(username='lead')
        cls.lead.groups.add(cls.leads)
        cls.author = User.objects.create(username='author')

        cls.light = Policy.objects.create(name='Prefix changes')
        cls.light.object_types.set([ObjectType.objects.get_for_model(Prefix)])
        PolicyRule.objects.create(policy=cls.light, name='One engineer', min_reviews=1).groups.set([cls.engineers])

        cls.heavy = Policy.objects.create(name='Device changes')
        cls.heavy.object_types.set([ObjectType.objects.get_for_model(Device)])
        PolicyRule.objects.create(policy=cls.heavy, name='One lead', min_reviews=1).groups.set([cls.leads])

        cls.Device = Device
        cls.Prefix = Prefix

    def setUp(self):
        self.branch = make_branch('scope', self._testMethodName)
        touch(self.branch, self.Prefix, 1, '10.0.0.0/24')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.author)
        self.cr.submit()
        self.cr.refresh_from_db()

    def attached(self):
        return sorted(p.name for p in self.cr.policies.all())

    def test_the_fixture_starts_with_the_light_policy_only(self):
        self.assertEqual(self.attached(), ['Prefix changes'])

    def test_a_branch_that_grows_a_new_object_type_picks_up_its_policy(self):
        """
        The bug this file exists for. The device policy must attach on the edit, not on the
        next sync and not never.
        """
        touch(self.branch, self.Device, 1, 'core-switch')

        self.cr.refresh_from_db()
        self.assertEqual(self.attached(), ['Device changes', 'Prefix changes'])

    def test_an_approval_given_before_the_growth_does_not_satisfy_the_new_policy(self):
        """
        The whole point. One engineer was enough for a prefix change; it must not be enough
        once the branch also rewrites a device.
        """
        approve(self.cr, self.engineer)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

        touch(self.branch, self.Device, 1, 'core-switch')

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertFalse(self.cr.evaluate().satisfied)

    def test_re_approving_after_the_growth_still_needs_the_lead(self):
        """
        The exploit end to end: the engineer approves twice and must still not get through.
        """
        approve(self.cr, self.engineer)
        touch(self.branch, self.Device, 1, 'core-switch')

        review = self.cr.reviews.get(reviewer=self.engineer)
        review.save(refresh_snapshot=True)
        self.cr.refresh_from_db()

        self.assertNotEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        self.assertFalse(require_approved_change_request(self.branch).permitted)

        approve(self.cr, self.lead)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_a_second_object_of_a_known_type_changes_nothing(self):
        """
        The guard that keeps a bulk edit cheap must not also skip a real change of scope.
        """
        touch(self.branch, self.Prefix, 2, '10.0.1.0/24')

        self.cr.refresh_from_db()
        self.assertEqual(self.attached(), ['Prefix changes'])

    def test_a_policy_whose_object_type_leaves_the_branch_is_detached(self):
        """
        Drift runs both ways. A policy asking for approvals the branch no longer needs is a
        different failure, but it is the same missing re-match.
        """
        diff = touch(self.branch, self.Device, 1, 'core-switch')
        self.cr.refresh_from_db()
        self.assertIn('Device changes', self.attached())

        diff.delete()
        sync_policies(self.cr)

        self.cr.refresh_from_db()
        self.assertEqual(self.attached(), ['Prefix changes'])

    def test_a_disabled_policy_is_not_pulled_in(self):
        self.heavy.enabled = False
        self.heavy.save()

        touch(self.branch, self.Device, 1, 'core-switch')

        self.cr.refresh_from_db()
        self.assertEqual(self.attached(), ['Prefix changes'])

    def test_a_completed_request_is_left_alone(self):
        """
        A merged request is a record. Re-matching it would rewrite history.
        """
        self.cr.status = ChangeRequestStatusChoices.COMPLETED
        self.cr.save(update_fields=['status'])

        touch(self.branch, self.Device, 1, 'core-switch')

        self.cr.refresh_from_db()
        self.assertEqual(self.attached(), ['Prefix changes'])


class MergeGateRematchesTest(TestCase):
    """
    The gate must not depend on the receiver having fired.

    It is the only moment the answer decides anything, so it re-matches for itself. These
    tests remove the binding behind its back, which is what any path the receiver misses
    would look like.
    """

    @classmethod
    def setUpTestData(cls):
        from dcim.models import Device
        from ipam.models import Prefix

        cls.engineers = Group.objects.create(name='Engineers')
        cls.engineer = User.objects.create(username='engineer')
        cls.engineer.groups.add(cls.engineers)
        cls.author = User.objects.create(username='author')

        cls.light = Policy.objects.create(name='Prefix changes')
        cls.light.object_types.set([ObjectType.objects.get_for_model(Prefix)])
        PolicyRule.objects.create(policy=cls.light, name='One engineer', min_reviews=1).groups.set([cls.engineers])

        cls.heavy = Policy.objects.create(name='Device changes')
        cls.heavy.object_types.set([ObjectType.objects.get_for_model(Device)])
        PolicyRule.objects.create(policy=cls.heavy, name='One lead', min_reviews=1).groups.set(
            [Group.objects.create(name='Leads')]
        )

        cls.Device = Device
        cls.Prefix = Prefix

    def setUp(self):
        self.branch = make_branch('gate', self._testMethodName)
        touch(self.branch, self.Prefix, 1, '10.0.0.0/24')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.author)
        self.cr.submit()
        approve(self.cr, self.engineer)
        self.cr.refresh_from_db()
        self.cr.checks.update(status='success')

    def test_an_approved_request_matching_its_policies_may_merge(self):
        self.assertTrue(require_approved_change_request(self.branch).permitted)

    def test_the_gate_refuses_when_a_matching_policy_is_not_attached(self):
        touch(self.branch, self.Device, 1, 'core-switch')

        # Behind the receiver's back, as any path it does not cover would leave things.
        ChangeRequestPolicy.objects.filter(change_request=self.cr, policy=self.heavy).delete()
        ChangeRequest.objects.filter(pk=self.cr.pk).update(status=ChangeRequestStatusChoices.APPROVED)

        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)

    def test_the_gate_reattaches_the_policy_it_was_missing(self):
        touch(self.branch, self.Device, 1, 'core-switch')
        ChangeRequestPolicy.objects.filter(change_request=self.cr, policy=self.heavy).delete()
        ChangeRequest.objects.filter(pk=self.cr.pk).update(status=ChangeRequestStatusChoices.APPROVED)

        require_approved_change_request(self.branch)

        self.assertIn('Device changes', {p.name for p in self.cr.policies.all()})
