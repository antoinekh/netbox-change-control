"""
Change windows and automatic merging.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone
from netbox.context import current_request
from netbox_branching.jobs import MergeBranchJob
from netbox_branching.models import Branch
from users.models import User

from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import ChangeRequest
from netbox_change_control.tests.base import ChangeControlTestCase, make_branch, pass_checks
from netbox_change_control.validators import require_approved_change_request


class WindowStateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')

    def _request(self, **kwargs):
        branch = make_branch('win', self._testMethodName)
        return ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, **kwargs)

    def test_no_window_is_always_open(self):
        cr = self._request()
        self.assertEqual(cr.window_state(), 'none')
        self.assertTrue(cr.window_is_open)

    def test_a_future_window_is_not_yet_open(self):
        cr = self._request(scheduled_start=timezone.now() + timedelta(hours=1))
        self.assertEqual(cr.window_state(), 'early')
        self.assertFalse(cr.window_is_open)

    def test_a_past_window_is_closed(self):
        cr = self._request(scheduled_end=timezone.now() - timedelta(hours=1))
        self.assertEqual(cr.window_state(), 'closed')
        self.assertFalse(cr.window_is_open)

    def test_a_current_window_is_open(self):
        cr = self._request(
            scheduled_start=timezone.now() - timedelta(hours=1),
            scheduled_end=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(cr.window_state(), 'open')
        self.assertTrue(cr.window_is_open)

    def test_a_start_alone_means_not_before(self):
        cr = self._request(scheduled_start=timezone.now() - timedelta(minutes=1))
        self.assertEqual(cr.window_state(), 'open')

    def test_the_window_must_close_after_it_opens(self):
        now = timezone.now()
        cr = self._request(scheduled_start=now, scheduled_end=now - timedelta(hours=1))
        with self.assertRaises(ValidationError):
            cr.full_clean()


class ApprovedRequestTestCase(ChangeControlTestCase):
    """
    An approved request with every other gate already clear, so a test only has to set the
    one thing it is about.
    """

    branch_prefix = 'gate'
    approved = True

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.privileged = User.objects.create(username='oncall', is_superuser=True)

    def setUp(self):
        super().setUp()
        pass_checks(self.cr)

    def _as(self, user):
        request = RequestFactory().post('/')
        request.user = user
        return current_request.set(request)


class WindowGateTest(ApprovedRequestTestCase):
    def test_an_approved_request_with_no_window_can_merge(self):
        self.assertTrue(require_approved_change_request(self.branch).permitted)

    def test_a_future_window_blocks_the_merge(self):
        self.cr.scheduled_start = timezone.now() + timedelta(hours=2)
        self.cr.save()
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('window opens', indicator.message)

    def test_a_closed_window_blocks_the_merge(self):
        self.cr.scheduled_end = timezone.now() - timedelta(hours=2)
        self.cr.save()
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('window closed', indicator.message)

    def test_the_override_permission_allows_a_merge_outside_the_window(self):
        self.cr.scheduled_start = timezone.now() + timedelta(hours=2)
        self.cr.save()
        token = self._as(self.privileged)
        try:
            self.assertTrue(require_approved_change_request(self.branch).permitted)
        finally:
            current_request.reset(token)

    def test_the_window_fails_closed_with_no_request(self):
        """
        A script merging at the wrong hour is exactly what a window exists to stop, so an
        absent request context must not bypass it.
        """
        self.cr.scheduled_start = timezone.now() + timedelta(hours=2)
        self.cr.save()
        current_request.set(None)
        self.assertFalse(require_approved_change_request(self.branch).permitted)


class AutoMergeTest(ApprovedRequestTestCase):
    """
    These mock MergeBranchJob.enqueue, which is the right thing to mock: the alternative is
    running a real branch merge. It does mean they cannot see a path that enqueues twice,
    because the guard against that reads the job queue and a mocked enqueue writes no Job row.
    test_automerge_once.py covers that with the real enqueue; do not delete it as a duplicate
    of these.
    """

    def _try(self):
        from netbox_change_control.automerge import try_auto_merge

        self.cr.refresh_from_db()
        return try_auto_merge(self.cr)

    def test_the_merge_is_enqueued_not_run_inline(self):
        """
        try_auto_merge is reached from a signal, so merging inline would run a whole branch
        merge inside the web request that submitted the final review.
        """
        from netbox_branching.jobs import MergeBranchJob

        self.cr.auto_merge = True
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as enqueue, patch.object(Branch, 'merge') as merge:
            self.assertTrue(self._try())
            enqueue.assert_called_once()
            merge.assert_not_called()
            self.assertEqual(enqueue.call_args.kwargs['instance'], self.branch)
            self.assertEqual(enqueue.call_args.kwargs['user'], self.requester)

    def test_nothing_happens_unless_the_request_opts_in(self):
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertFalse(self._try())
            merge.assert_not_called()

    def test_an_opted_in_approved_request_merges(self):
        self.cr.auto_merge = True
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertTrue(self._try())
            merge.assert_called_once()

    def test_it_waits_for_the_window(self):
        self.cr.auto_merge = True
        self.cr.scheduled_start = timezone.now() + timedelta(hours=1)
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertFalse(self._try())
            merge.assert_not_called()

    def test_it_waits_for_a_failing_check(self):
        self.cr.auto_merge = True
        self.cr.save()
        self.cr.checks.update(status=MergeCheckStatusChoices.FAILURE)
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertFalse(self._try())
            merge.assert_not_called()

    def test_it_does_not_merge_an_unapproved_request(self):
        self.cr.auto_merge = True
        self.cr.status = ChangeRequestStatusChoices.NEEDS_REVIEW
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertFalse(self._try())
            merge.assert_not_called()

    def test_the_global_switch_disables_it(self):
        self.cr.auto_merge = True
        self.cr.save()

        def cfg(name, setting, default=None):
            return False if setting == 'enable_auto_merge' else default

        with patch('netbox_change_control.automerge.get_plugin_config', cfg):
            with patch.object(MergeBranchJob, 'enqueue') as merge:
                self.assertFalse(self._try())
                merge.assert_not_called()

    def test_approved_during_the_day_merges_when_the_evening_window_opens(self):
        """
        The realistic timeline: approved at midday, window opens at 21:00.

        Approval alone must not merge, and nothing needs to happen again at 21:00 for the
        merge to occur. The periodic job notices the window has opened.
        """
        from netbox_change_control.automerge import run_due_auto_merges

        opens_at = timezone.now() + timedelta(hours=7)
        self.cr.auto_merge = True
        self.cr.scheduled_start = opens_at
        self.cr.scheduled_end = opens_at + timedelta(hours=2)
        self.cr.save()

        # Midday: approved, but the window is shut.
        with patch.object(MergeBranchJob, 'enqueue') as enqueue:
            self.assertFalse(self._try())
            self.assertEqual(run_due_auto_merges(), 0)
            enqueue.assert_not_called()

        # 21:00: nothing changed except the clock.
        later = opens_at + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=later):
            with patch.object(MergeBranchJob, 'enqueue') as enqueue:
                self.assertEqual(run_due_auto_merges(), 1)
                enqueue.assert_called_once()

    def test_a_branch_edit_before_the_window_opens_cancels_the_merge(self):
        """
        Approval is only valid for the branch state it was given against. If someone edits
        the branch during the wait, the approval goes stale and the evening merge must not
        happen.
        """
        from netbox_change_control.automerge import run_due_auto_merges

        opens_at = timezone.now() + timedelta(hours=7)
        self.cr.auto_merge = True
        self.cr.scheduled_start = opens_at
        self.cr.save()

        # The branch moves on while everyone waits for the window.
        self.cr.status = ChangeRequestStatusChoices.NEEDS_REVIEW
        self.cr.save(update_fields=['status'])

        later = opens_at + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=later):
            with patch.object(MergeBranchJob, 'enqueue') as enqueue:
                self.assertEqual(run_due_auto_merges(), 0)
                enqueue.assert_not_called()

    def test_the_periodic_job_picks_up_an_opened_window(self):
        from netbox_change_control.automerge import run_due_auto_merges

        self.cr.auto_merge = True
        self.cr.scheduled_start = timezone.now() - timedelta(minutes=1)
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertEqual(run_due_auto_merges(), 1)
            merge.assert_called_once()


class AutoMergeIntervalTest(TestCase):
    """
    The sweep interval is configurable, because it bounds how late a change window can fire
    and it costs one Job record per run.
    """

    def test_the_configured_default_is_ten_minutes(self):
        """
        The shipped default, read through the plugin config rather than mocked.
        """
        from netbox_change_control.jobs import _interval

        self.assertEqual(_interval(), 10)

    def test_a_nonsense_interval_is_refused_at_startup(self):
        """
        A bad value must fail loudly on boot rather than silently scheduling nothing.
        """
        from unittest.mock import patch

        from django.core.exceptions import ImproperlyConfigured

        from netbox_change_control.jobs import _interval

        for bad in (0, -5, 1.5, '60', None):
            with patch('netbox_change_control.jobs.get_plugin_config', return_value=bad):
                with self.assertRaises(ImproperlyConfigured):
                    _interval()

    def test_the_job_is_registered(self):
        from netbox.registry import registry

        from netbox_change_control.jobs import AutoMergeJob

        self.assertIn(AutoMergeJob, registry['system_jobs'])


class ShortWindowWarningTest(TestCase):
    """
    A window shorter than the sweep interval is a silent trap.

    Automatic merge has two triggers. The event trigger only fires when the last approval or
    the last check arrives while the window is already open. A request which is ready before
    its window opens depends entirely on the periodic sweep, and that sweep can step straight
    over a window shorter than its own interval. The change request must say so.
    """

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='short-window-requester')

    def _request(self, *, auto_merge=True, minutes=5, both_bounds=True):
        branch = make_branch('short', self._testMethodName)
        start = timezone.now() + timedelta(hours=1)
        return ChangeRequest.objects.create(
            branch=branch,
            title='T',
            requester=self.requester,
            auto_merge=auto_merge,
            scheduled_start=start,
            scheduled_end=start + timedelta(minutes=minutes) if both_bounds else None,
        )

    def _config(self, *, interval=15, enabled=True):
        """
        Patch the two settings the warning reads, leaving the rest at their defaults.
        """
        values = {'auto_merge_interval': interval, 'enable_auto_merge': enabled}

        def cfg(name, setting, default=None):
            return values.get(setting, default)

        return patch('netbox_change_control.automerge.get_plugin_config', cfg)

    def test_a_window_shorter_than_the_interval_is_reported(self):
        cr = self._request(minutes=5)
        with self._config(interval=15):
            warning = cr.auto_merge_window_warning
        self.assertIsNotNone(warning)
        self.assertEqual(warning.window_minutes, 5)
        self.assertEqual(warning.interval_minutes, 15)

    def test_a_window_as_long_as_the_interval_is_not_reported(self):
        """
        Runs are spaced one interval apart, so a window of exactly that length always
        contains one.
        """
        cr = self._request(minutes=15)
        with self._config(interval=15):
            self.assertIsNone(cr.auto_merge_window_warning)

    def test_nothing_is_reported_without_auto_merge(self):
        cr = self._request(auto_merge=False, minutes=1)
        with self._config(interval=15):
            self.assertIsNone(cr.auto_merge_window_warning)

    def test_nothing_is_reported_for_a_half_open_window(self):
        """
        One bound leaves the window open indefinitely in one direction, so a sweep finds it.
        """
        cr = self._request(both_bounds=False)
        with self._config(interval=15):
            self.assertIsNone(cr.auto_merge_window_warning)

    def test_nothing_is_reported_when_auto_merge_is_switched_off_globally(self):
        cr = self._request(minutes=1)
        with self._config(interval=15, enabled=False):
            self.assertIsNone(cr.auto_merge_window_warning)

    def test_an_unusable_interval_reports_nothing(self):
        """
        The warning stays quiet rather than raising; jobs.py is where a bad value is loud.
        """
        cr = self._request(minutes=1)
        for bad in (0, -5, 1.5, '60', None):
            with self._config(interval=bad):
                self.assertIsNone(cr.auto_merge_window_warning)

    def test_the_shipped_default_warns_on_a_five_minute_window(self):
        """
        The default interval is 10 minutes, so a shorter window is reachable from the UI.
        Read the real configuration here rather than mocking it.
        """
        cr = self._request(minutes=5)
        warning = cr.auto_merge_window_warning
        self.assertIsNotNone(warning)
        self.assertEqual(warning.interval_minutes, 10)


class ShortWindowAlertTest(TestCase):
    """
    The warning has to reach the person who set the window, not just the model layer.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='short-window-viewer', is_superuser=True)
        branch = Branch.objects.create(name='short-window-alert')
        start = timezone.now() + timedelta(hours=1)
        cls.cr = ChangeRequest.objects.create(
            branch=branch,
            title='Nightly',
            requester=cls.user,
            auto_merge=True,
            scheduled_start=start,
            scheduled_end=start + timedelta(minutes=5),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _config(self, interval):
        values = {'auto_merge_interval': interval, 'enable_auto_merge': True}

        def cfg(name, setting, default=None):
            return values.get(setting, default)

        return patch('netbox_change_control.automerge.get_plugin_config', cfg)

    def test_the_detail_page_shows_the_alert(self):
        with self._config(60):
            response = self.client.get(self.cr.get_absolute_url())
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('may never merge automatically', content)
        self.assertIn('Window too short', content)

    def test_the_detail_page_stays_quiet_when_the_window_is_long_enough(self):
        with self._config(1):
            response = self.client.get(self.cr.get_absolute_url())
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('may never merge automatically', content)
        self.assertNotIn('Window too short', content)
