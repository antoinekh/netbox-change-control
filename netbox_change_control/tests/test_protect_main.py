"""
Tests for protect_main.

The receivers refuse a write when there is no active branch, the model supports branching,
and the user lacks the bypass permission.
"""

import contextvars
import uuid
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from netbox.context import current_request
from netbox_branching.choices import BranchStatusChoices
from netbox_branching.models import Branch
from netbox_branching.signals import pre_merge, pre_revert
from users.models import User
from utilities.exceptions import AbortRequest

from netbox_change_control.models import Policy
from netbox_change_control.tests.base import make_branch


def _plugin_config(name, setting, default=None):
    """
    Stand-in for get_plugin_config which reports protect_main as enabled and unscoped.
    """
    values = {
        'protect_main': True,
        'protect_main_scope': [],
        'enforce_merge_gate': True,
    }
    return values.get(setting, default)


def _scoped_config(scope):
    """
    Build a get_plugin_config stand-in with protect_main limited to `scope`.
    """

    def _config(name, setting, default=None):
        values = {
            'protect_main': True,
            'protect_main_scope': scope,
            'enforce_merge_gate': True,
        }
        return values.get(setting, default)

    return _config


class ProtectMainTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='editor')
        cls.superuser = User.objects.create(username='root', is_superuser=True)

    def _request(self, user):
        request = RequestFactory().post('/')
        request.user = user
        # NetBox change logging reads request.id when writing ObjectChange rows.
        request.id = uuid.uuid4()
        return request

    def test_disabled_by_default_allows_writes(self):
        from dcim.models import Site

        # No patching: the default configuration leaves protect_main off.
        site = Site.objects.create(name='Open site', slug='open-site')
        self.assertIsNotNone(site.pk)

    def test_write_outside_a_branch_is_refused_when_enabled(self):
        from dcim.models import Site

        token = current_request.set(self._request(self.user))
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                with self.assertRaises(AbortRequest):
                    Site.objects.create(name='Blocked site', slug='blocked-site')
        finally:
            current_request.reset(token)

    def test_bypass_permission_allows_the_write(self):
        from dcim.models import Site

        token = current_request.set(self._request(self.superuser))
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                site = Site.objects.create(name='Bypass site', slug='bypass-site')
            self.assertIsNotNone(site.pk)
        finally:
            current_request.reset(token)

    def test_plugin_models_are_never_blocked(self):
        """
        Change-control records must stay writable on main, otherwise protect_main would stop
        anyone from opening a change request.
        """
        token = current_request.set(self._request(self.user))
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                policy = Policy.objects.create(name='Still writable')
            self.assertIsNotNone(policy.pk)
        finally:
            current_request.reset(token)

    def test_writes_without_a_request_are_allowed(self):
        """
        Migrations, scripts and background jobs run with no request. They are not interactive
        edits, so protect_main does not apply to them.
        """
        from dcim.models import Site

        current_request.set(None)
        with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
            site = Site.objects.create(name='Job site', slug='job-site')
        self.assertIsNotNone(site.pk)

    def test_delete_outside_a_branch_is_refused_when_enabled(self):
        from dcim.models import Site

        site = Site.objects.create(name='Doomed', slug='doomed')
        token = current_request.set(self._request(self.user))
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                with self.assertRaises(AbortRequest):
                    site.delete()
        finally:
            current_request.reset(token)


class BranchReplayTest(TestCase):
    """
    A merge or a revert replays the branch's changes on main as their original author.

    The merge permission and the merge gate already decided that these changes may reach main,
    so the author must not also need the bypass permission.
    """

    @classmethod
    def setUpTestData(cls):
        from dcim.models import Site

        cls.author = User.objects.create(username='branch-author')
        cls.branch = make_branch('protect-main', 'replay')
        cls.site = Site.objects.create(name='Replayed', slug='replayed')

    def _write_as_author(self):
        request = RequestFactory().get('/')
        request.user = self.author
        request.id = uuid.uuid4()
        token = current_request.set(request)
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                self.site.description = 'Changed'
                self.site.save()
        finally:
            current_request.reset(token)

    def _set_status(self, status):
        Branch.objects.filter(pk=self.branch.pk).update(status=status)

    def _replay(self, signal, status):
        """
        The order Branch.merge() and Branch.revert() follow: the signal, the status, the replay.
        """
        signal.send(sender=Branch, branch=self.branch, user=self.author)
        self._set_status(status)
        self._write_as_author()

    def test_a_merge_writes_the_author_change_to_main(self):
        contextvars.copy_context().run(self._replay, pre_merge, BranchStatusChoices.MERGING)

        self.site.refresh_from_db()
        self.assertEqual(self.site.description, 'Changed')

    def test_a_revert_writes_to_main(self):
        contextvars.copy_context().run(self._replay, pre_revert, BranchStatusChoices.REVERTING)

        self.site.refresh_from_db()
        self.assertEqual(self.site.description, 'Changed')

    def test_a_direct_write_during_another_merge_is_refused(self):
        contextvars.copy_context().run(pre_merge.send, sender=Branch, branch=self.branch, user=self.author)
        self._set_status(BranchStatusChoices.MERGING)

        with self.assertRaises(AbortRequest):
            self._write_as_author()

    def test_a_failed_merge_ends_the_exemption(self):
        def merge_then_fail():
            pre_merge.send(sender=Branch, branch=self.branch, user=self.author)
            # A failed merge sends no post_merge signal; it only puts the branch back to ready.
            self._set_status(BranchStatusChoices.READY)
            with self.assertRaises(AbortRequest):
                self._write_as_author()

        contextvars.copy_context().run(merge_then_fail)


class ProtectMainScopeTest(TestCase):
    """
    protect_main_scope limits enforcement to named models, so a team can require branches
    for circuits while leaving the rest of NetBox editable on main.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='scoped-editor')

    def _request(self):
        request = RequestFactory().post('/')
        request.user = self.user
        request.id = uuid.uuid4()
        return request

    def _create_site(self, name):
        from dcim.models import Site

        return Site.objects.create(name=name, slug=name.lower().replace(' ', '-'))

    def _create_provider(self, name):
        from circuits.models import Provider

        return Provider.objects.create(name=name, slug=name.lower().replace(' ', '-'))

    def test_model_inside_the_scope_is_blocked(self):
        token = current_request.set(self._request())
        try:
            with patch(
                'netbox_change_control.receivers.protect_main.get_plugin_config',
                _scoped_config(['circuits.*']),
            ):
                with self.assertRaises(AbortRequest):
                    self._create_provider('Blocked provider')
        finally:
            current_request.reset(token)

    def test_model_outside_the_scope_is_allowed(self):
        token = current_request.set(self._request())
        try:
            with patch(
                'netbox_change_control.receivers.protect_main.get_plugin_config',
                _scoped_config(['circuits.*']),
            ):
                site = self._create_site('Allowed site')
            self.assertIsNotNone(site.pk)
        finally:
            current_request.reset(token)

    def test_an_exact_model_entry_matches(self):
        token = current_request.set(self._request())
        try:
            with patch(
                'netbox_change_control.receivers.protect_main.get_plugin_config',
                _scoped_config(['circuits.provider']),
            ):
                with self.assertRaises(AbortRequest):
                    self._create_provider('Exactly named')
        finally:
            current_request.reset(token)

    def test_an_exact_entry_does_not_protect_siblings_in_the_app(self):
        from circuits.models import CircuitType

        token = current_request.set(self._request())
        try:
            with patch(
                'netbox_change_control.receivers.protect_main.get_plugin_config',
                _scoped_config(['circuits.provider']),
            ):
                circuit_type = CircuitType.objects.create(name='Allowed type', slug='allowed-type')
            self.assertIsNotNone(circuit_type.pk)
        finally:
            current_request.reset(token)

    def test_an_empty_scope_protects_everything(self):
        token = current_request.set(self._request())
        try:
            with patch(
                'netbox_change_control.receivers.protect_main.get_plugin_config',
                _scoped_config([]),
            ):
                with self.assertRaises(AbortRequest):
                    self._create_site('Blocked site')
        finally:
            current_request.reset(token)


class RefusalReachesTheUserTest(TestCase):
    """
    The refusal has to say why, on the page the user is working on.

    Raising PermissionDenied handed NetBox a bare "Access Denied" page and threw our message
    away, so the user saw only "You do not have permission to access this page". That reads
    like a broken permission rather than a deliberate policy, and says nothing about opening
    a branch. AbortRequest is the mechanism NetBox provides for a signal receiver to refuse
    a write and still be heard.
    """

    @classmethod
    def setUpTestData(cls):
        from dcim.models import Site
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission

        cls.user = User.objects.create(username='site-editor')
        # A real permission on sites, so the refusal below is protect_main and nothing else.
        permission = ObjectPermission.objects.create(name='edit-sites', actions=['view', 'add', 'change', 'delete'])
        permission.users.add(cls.user)
        permission.object_types.set([ContentType.objects.get_for_model(Site)])

    def test_the_message_is_carried_on_the_exception(self):
        """
        NetBox's edit view reads `.message` and adds it to the form. An exception without one
        renders as an empty error.
        """
        from dcim.models import Site

        request = RequestFactory().post('/')
        request.user = self.user
        request.id = uuid.uuid4()

        token = current_request.set(request)
        try:
            with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
                with self.assertRaises(AbortRequest) as caught:
                    Site.objects.create(name='Refused', slug='refused')
        finally:
            current_request.reset(token)

        self.assertTrue(hasattr(caught.exception, 'message'))
        self.assertIn('Create a branch', caught.exception.message)

    def test_the_edit_form_shows_the_message_rather_than_a_403(self):
        """
        The path the screenshot documents: submit the form, stay on the page, read why.
        """
        from dcim.models import Site

        site = Site.objects.create(name='Existing', slug='existing')
        self.client.force_login(self.user)

        with patch('netbox_change_control.receivers.protect_main.get_plugin_config', _plugin_config):
            response = self.client.post(
                site.get_absolute_url() + 'edit/',
                {'name': 'Renamed', 'slug': 'existing', 'status': 'active'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('Direct changes to main are disabled', response.content.decode())
        site.refresh_from_db()
        self.assertEqual(site.name, 'Existing')
