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

from django.conf import settings
from django.test import TestCase, override_settings
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


# The alert and the card render the same content in a different frame, so a test has to look
# at the frame to tell which one it got. These markers are how each wrapper writes its own
# heading, which the other never does. The icons are no use: the Change Control menu carries
# the same one on every page.
ALERT_MARKER = '<strong>Change request</strong>'
CARD_MARKER = '<span>Change request</span>'
NO_REQUEST_ALERT_MARKER = '<strong>No change request</strong>'
NO_REQUEST_CARD_MARKER = '<h5 class="card-header text-yellow">No change request</h5>'


def placement(*names):
    """
    Run a test with the branch page placed where it says.

    `override_settings` replaces PLUGINS_CONFIG wholesale, and netbox-branching reads its own
    settings out of that same dictionary, so the override has to carry every plugin's
    configuration and change one key of it.
    """
    config = {plugin: dict(plugin_config) for plugin, plugin_config in settings.PLUGINS_CONFIG.items()}
    config['netbox_change_control']['branch_page_placement'] = list(names)
    return override_settings(PLUGINS_CONFIG=config)


class BranchPageTestCase(TestCase):
    """
    A branch page, a policy nobody has satisfied, and an administrator looking at it.
    """

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
        cr.submit()
        cr.refresh_from_db()
        return branch, cr


class BranchPageTest(BranchPageTestCase):
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


class PlacementTest(BranchPageTestCase):
    """
    Where the panel appears is configuration, and the two placements are meant to be compared,
    so naming both has to show both rather than one winning.
    """

    def test_the_card_is_what_the_plugin_ships(self):
        """
        Read from `default_settings` rather than from the running configuration, which a
        deployment is free to change, and this one does to compare the two.
        """
        from netbox_change_control import ChangeControlConfig

        self.assertEqual(ChangeControlConfig.default_settings['branch_page_placement'], ['right_page'])

    @placement('alerts')
    def test_the_alert_sits_across_the_top(self):
        branch, _cr = self.with_request('default')

        html = self.page(branch)

        self.assertIn(ALERT_MARKER, html)
        self.assertNotIn(CARD_MARKER, html)

    @placement('right_page')
    def test_the_right_hand_column_replaces_the_alert(self):
        branch, cr = self.with_request('right')

        html = self.page(branch)

        self.assertIn(CARD_MARKER, html)
        self.assertNotIn(ALERT_MARKER, html)
        self.assertIn(cr.get_absolute_url(), html)

    @placement('alerts', 'right_page')
    def test_naming_both_shows_both(self):
        branch, _cr = self.with_request('both')

        html = self.page(branch)

        self.assertIn(ALERT_MARKER, html)
        self.assertIn(CARD_MARKER, html)

    @placement()
    def test_an_empty_list_leaves_the_branch_page_alone(self):
        branch, cr = self.with_request('off')

        html = self.page(branch)

        self.assertNotIn(ALERT_MARKER, html)
        self.assertNotIn(CARD_MARKER, html)
        self.assertNotIn(cr.get_absolute_url(), html)

    @placement('right_page')
    def test_the_offer_to_open_one_follows_the_placement(self):
        branch = ready_branch('bpage', 'right-none')

        html = self.page(branch)

        self.assertIn(NO_REQUEST_CARD_MARKER, html)
        self.assertNotIn(NO_REQUEST_ALERT_MARKER, html)
        self.assertIn(f'/change-requests/add/?branch={branch.pk}', html)

    @placement('left_page')
    def test_a_name_that_is_not_a_placement_is_skipped_rather_than_raising(self):
        """
        A typo in configuration must not take the branch page down with it, which is how the
        built-in check selection behaves as well.
        """
        branch, _cr = self.with_request('typo')

        with self.assertLogs('netbox.plugins.netbox_change_control', level='WARNING') as logs:
            html = self.page(branch)

        self.assertNotIn(ALERT_MARKER, html)
        self.assertNotIn(CARD_MARKER, html)
        self.assertIn('left_page', logs.output[0])
