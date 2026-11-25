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

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    ChangeWindowState,
    HistoryActionChoices,
    MergeCheckStatusChoices,
)
from netbox_change_control.models import ChangeRequest
from netbox_change_control.tests.base import ChangeControlTestCase, grant_merge, make_branch, pass_checks
from netbox_change_control.validators import require_approved_change_request


class WindowStateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')

    def _request(self, **kwargs):
        branch = make_branch('win', self._testMethodName)
        return ChangeRequest.objects.create(branch=branch, title='T', requester=self.requester, **kwargs)

    def test_the_state_of_each_window(self):
        now = timezone.now()
        hour = timedelta(hours=1)
        cases = (
            ({}, ChangeWindowState.NONE, True),
            ({'scheduled_start': now + hour}, ChangeWindowState.EARLY, False),
            ({'scheduled_end': now - hour}, ChangeWindowState.CLOSED, False),
            ({'scheduled_start': now - hour, 'scheduled_end': now + hour}, ChangeWindowState.OPEN, True),
            # A start alone means "not before".
            ({'scheduled_start': now - timedelta(minutes=1)}, ChangeWindowState.OPEN, True),
        )
        for window, state, is_open in cases:
            with self.subTest(window=window):
                cr = ChangeRequest(title='T', requester=self.requester, **window)
                self.assertEqual((cr.window_state(), cr.window_is_open), (state, is_open))

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
        self.reschedule(scheduled_start=timezone.now() + timedelta(hours=2))
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('window opens', indicator.message)

    def test_a_closed_window_blocks_the_merge(self):
        self.reschedule(scheduled_end=timezone.now() - timedelta(hours=2))
        indicator = require_approved_change_request(self.branch)
        self.assertFalse(indicator.permitted)
        self.assertIn('window closed', indicator.message)

    def test_the_override_permission_allows_a_merge_outside_the_window(self):
        self.reschedule(scheduled_start=timezone.now() + timedelta(hours=2))
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
        self.reschedule(scheduled_start=timezone.now() + timedelta(hours=2))
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

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # An automatic merge runs as the requester, who must be allowed to merge.
        grant_merge(cls.requester)

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
        self.reschedule(scheduled_start=timezone.now() + timedelta(hours=1))
        self.cr.auto_merge = True
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
        self.reschedule(scheduled_start=opens_at, scheduled_end=opens_at + timedelta(hours=2))
        self.cr.auto_merge = True
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
        self.reschedule(scheduled_start=opens_at)
        self.cr.auto_merge = True
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

        self.reschedule(scheduled_start=timezone.now() - timedelta(minutes=1))
        self.cr.auto_merge = True
        self.cr.save()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            self.assertEqual(run_due_auto_merges(), 1)
            merge.assert_called_once()


class AutoMergeStopsTest(ApprovedRequestTestCase):
    """
    An automatic merge stops after a failed merge, and records a window that closed first.
    """

    def setUp(self):
        super().setUp()
        grant_merge(self.requester)
        self.cr.auto_merge = True
        self.cr.save()

    def _fail_a_merge(self):
        import uuid

        from core.choices import JobStatusChoices
        from core.models import Job
        from django.contrib.contenttypes.models import ContentType

        job = Job.objects.create(
            name=MergeBranchJob.name,
            object_type=ContentType.objects.get_for_model(Branch),
            object_id=self.branch.pk,
            status=JobStatusChoices.STATUS_RUNNING,
            user=self.requester,
            job_id=uuid.uuid4(),
        )
        job.terminate(status=JobStatusChoices.STATUS_ERRORED, error='boom')

    def _try(self):
        from netbox_change_control.automerge import try_auto_merge

        self.cr.refresh_from_db()
        with patch.object(MergeBranchJob, 'enqueue') as merge:
            try_auto_merge(self.cr)
        return merge.called

    def test_a_failed_merge_is_recorded(self):
        self._fail_a_merge()

        entry = self.cr.history.last()
        self.assertEqual(entry.action, HistoryActionChoices.MERGE_FAILED)
        self.assertEqual(entry.user, self.requester)

    def test_a_failed_merge_stops_the_automatic_merge(self):
        self._fail_a_merge()

        self.assertFalse(self._try())

    def test_a_new_approval_resumes_it(self):
        self._fail_a_merge()

        with patch.object(MergeBranchJob, 'enqueue'):
            self.cr.return_to_draft()
            self.cr.submit()
        pass_checks(self.cr)

        self.assertTrue(self._try())

    def test_a_closed_window_is_recorded_once(self):
        from netbox_change_control.automerge import run_due_auto_merges

        self.reschedule(scheduled_end=timezone.now() - timedelta(minutes=5))

        run_due_auto_merges()
        run_due_auto_merges()

        self.assertEqual(self.cr.history.filter(action=HistoryActionChoices.WINDOW_CLOSED).count(), 1)

    def test_a_closed_window_notifies_the_requester(self):
        from extras.models import Notification

        from netbox_change_control.automerge import run_due_auto_merges
        from netbox_change_control.events import CHANGE_REQUEST_WINDOW_CLOSED

        self.reschedule(scheduled_end=timezone.now() - timedelta(minutes=5))

        run_due_auto_merges()

        self.assertTrue(
            Notification.objects.filter(user=self.requester, event_type=CHANGE_REQUEST_WINDOW_CLOSED).exists()
        )

    def test_a_closed_window_is_not_recorded_from_a_stale_read(self):
        from netbox_change_control.lifecycle import record_closed_window

        closed = timezone.now() - timedelta(minutes=5)
        self.reschedule(scheduled_end=closed)
        changes = (
            ('merged', {'status': ChangeRequestStatusChoices.COMPLETED}),
            ('abandoned', {'status': ChangeRequestStatusChoices.ABANDONED}),
            ('automatic merge turned off', {'auto_merge': False}),
            ('window moved later', {'scheduled_end': timezone.now() + timedelta(hours=1)}),
        )
        for name, fields in changes:
            with self.subTest(name):
                stale = ChangeRequest.objects.get(pk=self.cr.pk)
                ChangeRequest.objects.filter(pk=self.cr.pk).update(**fields)

                recorded = record_closed_window(stale)
                entries = self.cr.history.filter(action=HistoryActionChoices.WINDOW_CLOSED)
                self.assertFalse(recorded)
                self.assertFalse(entries.exists())

                # An entry would cover this window and hide the next case, so each starts clean.
                entries.delete()
                ChangeRequest.objects.filter(pk=self.cr.pk).update(
                    status=ChangeRequestStatusChoices.APPROVED, auto_merge=True, scheduled_end=closed
                )

    def test_a_window_moved_later_is_recorded_again_when_it_closes(self):
        from netbox_change_control.automerge import run_due_auto_merges

        self.reschedule(scheduled_end=timezone.now() - timedelta(minutes=5))
        run_due_auto_merges()

        later = timezone.now() + timedelta(hours=1)
        self.reschedule(scheduled_end=later)
        with patch('django.utils.timezone.now', return_value=later + timedelta(minutes=5)):
            run_due_auto_merges()

        self.assertEqual(self.cr.history.filter(action=HistoryActionChoices.WINDOW_CLOSED).count(), 2)

    def test_a_window_still_to_open_does_not_stop_it(self):
        self.cr.scheduled_start = timezone.now() + timedelta(hours=1)

        self.assertIsNone(self.cr.auto_merge_stopped)


class WindowChangeTest(ChangeControlTestCase):
    """
    The reviewers approve the slot as well as the content, so a new window needs new approvals.
    """

    branch_prefix = 'reschedule'
    approved = True

    def _edit(self, **fields):
        cr = ChangeRequest.objects.get(pk=self.cr.pk)
        for name, value in fields.items():
            setattr(cr, name, value)
        cr.save()
        cr.refresh_from_db()
        return cr

    def test_a_new_window_sends_the_request_back_to_review(self):
        cr = self._edit(scheduled_start=timezone.now() + timedelta(hours=2))

        self.assertEqual(cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertTrue(cr.history.filter(action=HistoryActionChoices.WINDOW_CHANGED).exists())

    def test_a_review_of_the_new_window_approves_it_again(self):
        self._edit(scheduled_start=timezone.now() + timedelta(hours=2))

        self.cr.reviews.get(reviewer=self.reviewer).save(refresh_snapshot=True)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_another_edit_keeps_the_approval(self):
        cr = self._edit(title='Renamed')

        self.assertEqual(cr.status, ChangeRequestStatusChoices.APPROVED)


class AutoMergeIntervalTest(TestCase):
    """
    The sweep interval is configurable, because it bounds how late a change window can fire
    and it costs one Job record per run.
    """

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
        branch = make_branch('short', f'{self._testMethodName}-{ChangeRequest.objects.count()}')
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

    def test_nothing_is_reported_when_the_sweep_can_be_relied_on(self):
        cases = (
            # Sweeps are one interval apart, so a window of exactly that length contains one.
            ('as long as the interval', {'minutes': 15}, {}),
            ('no automatic merge', {'auto_merge': False, 'minutes': 1}, {}),
            # One bound leaves the window open in one direction, so a sweep finds it.
            ('half-open window', {'both_bounds': False}, {}),
            ('automatic merge switched off', {'minutes': 1}, {'enabled': False}),
            # The warning stays quiet; the job is where a bad interval is loud.
            *((f'interval {bad!r}', {'minutes': 1}, {'interval': bad}) for bad in (0, -5, 1.5, '60', None)),
        )
        for name, request, config in cases:
            with self.subTest(name):
                cr = self._request(**request)
                with self._config(**{'interval': 15, **config}):
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
