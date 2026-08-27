"""
Other plugins must be able to extend this plugin's pages.

NetBox injects third-party content through PluginTemplateExtension. Core object views get
those hooks from the declarative layout, but a view that renders its own template must call
the tags itself, or the page is silently closed to extension.
"""

from django.test import TestCase
from django.urls import reverse
from netbox.plugins import PluginTemplateExtension
from netbox.registry import registry
from netbox_branching.models import Branch
from users.models import User

from netbox_change_control.models import ChangeRequest, MergeCheck, Policy, Review
from netbox_change_control.tests.base import make_policy

MARKERS = {
    'left_page': 'EXTENSION-LEFT',
    'right_page': 'EXTENSION-RIGHT',
    'full_width_page': 'EXTENSION-FULL',
    'buttons': 'EXTENSION-BUTTONS',
    'alerts': 'EXTENSION-ALERTS',
}


def _hook(marker):
    """
    Build one template extension method that stamps `marker`.
    """

    def render(self):
        return f'<div>{marker}</div>'

    return render


def _extension_for(label):
    """
    Build a template extension that stamps a recognisable marker in every hook.
    """
    return type(
        'ProbeExtension',
        (PluginTemplateExtension,),
        {
            'models': [label],
            **{method: _hook(marker) for method, marker in MARKERS.items()},
        },
    )


class PageExtensibilityTest(TestCase):
    """
    Each detail page is rendered with a probe extension registered, and must contain every
    marker.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='viewer', is_superuser=True)

        cls.policy, cls.rule = make_policy('Extensible')
        cls.branch = Branch.objects.create(name='extensible')
        cls.requester = User.objects.create(username='requester')
        cls.change_request = ChangeRequest.objects.create(branch=cls.branch, title='T', requester=cls.requester)
        cls.review = Review.objects.create(change_request=cls.change_request, reviewer=cls.user, decision='comment')
        cls.check = MergeCheck.objects.create(change_request=cls.change_request, name='probe', label='Probe')

    def setUp(self):
        self.client.force_login(self.user)
        self._registered = []

    def tearDown(self):
        for label, extension in self._registered:
            registry['plugins']['template_extensions'][label].remove(extension)

    def _register(self, label):
        extension = _extension_for(label)
        registry['plugins']['template_extensions'][label].append(extension)
        self._registered.append((label, extension))

    def _assert_extensible(self, label, url):
        self._register(label)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f'{url} did not render')
        content = response.content.decode()
        missing = [m for m in MARKERS.values() if m not in content]
        self.assertEqual(missing, [], f'{url} is missing {missing}')

    def test_the_change_request_page_is_extensible(self):
        self._assert_extensible('netbox_change_control.changerequest', self.change_request.get_absolute_url())

    def test_the_policy_page_is_extensible(self):
        self._assert_extensible('netbox_change_control.policy', self.policy.get_absolute_url())

    def test_the_policy_rule_page_is_extensible(self):
        self._assert_extensible('netbox_change_control.policyrule', self.rule.get_absolute_url())

    def test_the_review_page_is_extensible(self):
        self._assert_extensible(
            'netbox_change_control.review',
            reverse('plugins:netbox_change_control:review', args=[self.review.pk]),
        )

    def test_the_merge_check_page_is_extensible(self):
        self._assert_extensible(
            'netbox_change_control.mergecheck',
            reverse('plugins:netbox_change_control:mergecheck', args=[self.check.pk]),
        )

    def test_an_unscoped_extension_reaches_our_pages(self):
        """
        An extension that names no models runs on every object view, so it must reach ours.
        """
        extension = _extension_for('netbox_change_control.changerequest')
        extension.models = []
        registry['plugins']['template_extensions'][None].append(extension)
        self._registered.append((None, extension))

        response = self.client.get(self.change_request.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(MARKERS['left_page'], content)


class ListViewExtensibilityTest(TestCase):
    """
    `list_buttons` is the list-view counterpart of `buttons`. Our list views use NetBox's
    generic template, so it should work without any change; this pins that.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='lister', is_superuser=True)
        Policy.objects.create(name='Listed')

    def setUp(self):
        self.client.force_login(self.user)
        self.extension = type(
            'ListProbe',
            (PluginTemplateExtension,),
            {
                'models': ['netbox_change_control.policy'],
                'list_buttons': lambda self: '<div>EXTENSION-LIST-BUTTONS</div>',
            },
        )
        registry['plugins']['template_extensions']['netbox_change_control.policy'].append(self.extension)

    def tearDown(self):
        registry['plugins']['template_extensions']['netbox_change_control.policy'].remove(self.extension)

    def test_the_policy_list_accepts_list_buttons(self):
        response = self.client.get(reverse('plugins:netbox_change_control:policy_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('EXTENSION-LIST-BUTTONS', response.content.decode())


class TabRegistrationTest(TestCase):
    """
    Another plugin can attach a tab to our models with register_model_view. Nothing in this
    plugin has to allow it, but a change to our views could break it, so it is pinned.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='tabber', is_superuser=True)
        cls.branch = Branch.objects.create(name='tabbed')
        cls.requester = User.objects.create(username='req')
        cls.change_request = ChangeRequest.objects.create(branch=cls.branch, title='T', requester=cls.requester)

    def test_our_models_accept_a_registered_view(self):
        from netbox.views import generic
        from utilities.views import ViewTab, register_model_view

        @register_model_view(ChangeRequest, 'probe-tab', path='probe-tab')
        class ProbeTabView(generic.ObjectView):
            queryset = ChangeRequest.objects.all()
            template_name = 'netbox_change_control/changerequest.html'
            tab = ViewTab(label='Probe')

        # Registration is what matters: NetBox builds the tab list from the registry, which
        # holds a list of view descriptors rather than a mapping.
        from netbox.registry import registry as reg

        names = [v['name'] for v in reg['views']['netbox_change_control']['changerequest']]
        self.assertIn('probe-tab', names)


class DashboardWidgetRegistrationTest(TestCase):
    """
    NetBox auto-imports a plugin's navigation, search and template_content modules, but not
    its dashboard module. The widget was therefore never registered: it was absent from
    "Add widget", and a dashboard already holding one failed to load and silently fell back
    to the default layout.
    """

    def test_the_my_reviews_widget_is_registered(self):
        from netbox.registry import registry

        from netbox_change_control.dashboard import MyReviewsWidget

        self.assertIn('netbox_change_control.MyReviewsWidget', registry['widgets'])
        self.assertIs(registry['widgets']['netbox_change_control.MyReviewsWidget'], MyReviewsWidget)

    def test_a_stored_layout_referring_to_it_resolves(self):
        """
        This is the path that broke: Dashboard.get_layout() looks the class up by name.
        """
        from extras.models import Dashboard
        from users.models import User

        from netbox_change_control.dashboard import MyReviewsWidget

        user = User.objects.create(username='dashboard-owner')
        dashboard = Dashboard(user=user)
        dashboard.add_widget(MyReviewsWidget(title=str(MyReviewsWidget.default_title)))
        dashboard.save()

        widgets = Dashboard.objects.get(user=user).get_layout()
        self.assertEqual(len(widgets), 1)
        self.assertIsInstance(widgets[0], MyReviewsWidget)
