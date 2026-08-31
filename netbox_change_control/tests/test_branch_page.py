"""
What netbox-branching's own branch page says about change control.

A branch and its change request are two halves of one job, and they only pointed one way. The
change request page carries the merge button; the branch page said nothing, so somebody who
had just finished working in a branch had to leave it and find the request by name in another
menu, and somebody whose merge was refused was told the reason on the merge form as plain text
with nothing to click.

These pin what the injected content says, because it is the only part of the plugin that
renders inside somebody else's template and would break silently if that template moved.
"""

from django.test import TestCase
from netbox_branching.choices import BranchStatusChoices
from netbox_branching.models import Branch
from users.models import Group, ObjectPermission, User

from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Policy, PolicyRule
from netbox_change_control.tests.base import approve, make_branch


def ready_branch(prefix, suffix):
    """
    A branch the interface would treat as workable. Tests create branches without provisioning
    a schema, which leaves them in `new`, and a branch that is not ready yet has nothing to
    say about merging.
    """
    branch = make_branch(prefix, suffix)
    Branch.objects.filter(pk=branch.pk).update(status=BranchStatusChoices.READY)
    branch.refresh_from_db()
    return branch


class BranchPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='admin', is_superuser=True)
        cls.group = Group.objects.create(name='Leads')
        cls.lead = User.objects.create(username='lead')
        cls.lead.groups.add(cls.group)
        cls.policy = Policy.objects.create(name='Needs a lead')
        PolicyRule.objects.create(policy=cls.policy, name='One lead', min_reviews=1).groups.set([cls.group])

    def setUp(self):
        self.client.force_login(self.admin)

    def page(self, branch):
        response = self.client.get(branch.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def with_request(self, suffix, **kwargs):
        branch = ready_branch('bpage', suffix)
        cr = ChangeRequest.objects.create(branch=branch, requester=self.admin, **kwargs)
        ChangeRequestPolicy.objects.create(change_request=cr, policy=self.policy)
        cr.refresh_from_db()
        return branch, cr

    def test_the_branch_page_links_to_its_change_request(self):
        branch, cr = self.with_request('link', title='Upgrade the access switch', ref='CHG0012345')
        html = self.page(branch)

        self.assertIn(cr.get_absolute_url(), html)
        self.assertIn('Upgrade the access switch', html)
        self.assertIn('CHG0012345', html)

    def test_it_names_what_is_still_outstanding(self):
        """
        The reason the merge is refused, and who can clear it. Branching's merge form states
        the refusal; only this says what to do about it.
        """
        branch, _cr = self.with_request('outstanding', title='Blocked one')
        html = self.page(branch)

        self.assertIn('One lead', html)
        self.assertIn('0 / 1', html)
        self.assertIn('lead', html)

    def test_a_ready_branch_says_so(self):
        branch, cr = self.with_request('ready', title='Ready one')
        approve(cr, self.lead)
        cr.refresh_from_db()
        self.assertTrue(cr.is_ready_to_merge)

        html = self.page(branch)

        self.assertIn('Every gate is satisfied', html)

    def test_a_branch_with_no_change_request_is_told_to_open_one(self):
        branch = ready_branch('bpage', 'none')

        html = self.page(branch)

        self.assertIn('No change request', html)
        self.assertIn(f'/change-requests/add/?branch={branch.pk}', html)

    def test_a_branch_that_is_not_ready_yet_is_left_alone(self):
        """
        A branch still provisioning cannot merge for reasons that have nothing to do with
        change control, so nagging about a change request there is noise.
        """
        branch = make_branch('bpage', 'provisioning')
        self.assertFalse(branch.ready)

        html = self.page(branch)

        self.assertNotIn('No change request', html)

    def test_the_offer_to_open_one_is_permission_gated(self):
        """
        Somebody who cannot open a change request is told why the branch is blocked, but not
        offered a button that would refuse them.
        """
        from django.contrib.contenttypes.models import ContentType

        branch = ready_branch('bpage', 'noperm')
        viewer = User.objects.create(username='viewer')
        permission = ObjectPermission.objects.create(name='see branches', actions=['view'])
        permission.object_types.add(ContentType.objects.get_for_model(Branch))
        permission.users.add(viewer)
        self.client.force_login(viewer)

        html = self.page(branch)

        self.assertIn('No change request', html)
        self.assertNotIn('/change-requests/add/', html)
