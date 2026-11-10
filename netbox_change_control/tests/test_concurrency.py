"""
Status transitions made from a status that is no longer current.

Every transition decides from the current status. Two requests acting on the same change
request each load it, so without a lock both can start from the same old status: both announce
the transition to the event rules, or one undoes what the other just did. A double click on
Submit is enough to do it.

The first class holds a stale copy in a single thread, which is deterministic. The second runs
real concurrent transactions on separate connections, with the timing forced by barriers.
"""

import threading
import time
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge
from users.models import Group, User

from netbox_change_control import policy, signal_receivers
from netbox_change_control.checks import BUILTIN_CHECKS
from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.events import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_REVIEW_REQUESTED,
)
from netbox_change_control.lifecycle import refresh_status
from netbox_change_control.models import ChangeComment, ChangeRequest, ChangeRequestPolicy, Review
from netbox_change_control.tests.base import ChangeControlTestCase, make_policy

# How long a thread waits for the other at a barrier. With the lock, the other thread is held
# at the lock and never arrives, so a correct run spends this long once per wait.
WAIT = 2


def _emitted(flush):
    return [event['event_type'] for call in flush.call_args_list for event in call.args[0]]


class StaleCopyTest(ChangeControlTestCase):
    branch_prefix = 'stale'

    def _stale(self):
        return ChangeRequest.objects.get(pk=self.cr.pk)

    def test_a_second_submit_of_the_same_draft_is_refused(self):
        ChangeRequest.objects.filter(pk=self.cr.pk).update(status=ChangeRequestStatusChoices.DRAFT)
        first, second = self._stale(), self._stale()

        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(first.submit())
                self.assertFalse(second.submit())

        self.assertEqual(_emitted(flush).count(CHANGE_REQUEST_REVIEW_REQUESTED), 1)

    def test_a_stale_abandon_cannot_undo_a_merge(self):
        stale = self._stale()
        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)

        self.assertFalse(stale.abandon())
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_a_stale_return_to_draft_cannot_undo_a_merge(self):
        stale = self._stale()
        post_merge.send(sender=Branch, branch=self.branch, user=self.requester)

        self.assertFalse(stale.return_to_draft())
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.COMPLETED)

    def test_a_stale_refresh_cannot_revive_an_abandoned_request(self):
        stale = self._stale()
        self._stale().abandon()
        self._approve()

        refresh_status(stale)

        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_a_second_reopen_of_the_same_request_is_refused(self):
        self.cr.abandon()
        first, second = self._stale(), self._stale()

        self.assertTrue(first.reopen())
        self.assertFalse(second.reopen())

    def test_the_lock_does_not_stop_child_rows_from_committing(self):
        """
        A plain FOR UPDATE would hold back every transaction adding a review, a check or a
        comment to this request until the transition commits, and deadlock if the transition
        then waits on one of them. NO KEY UPDATE is the lock the status write takes anyway.
        """
        with CaptureQueriesContext(connection) as queries:
            with transaction.atomic():
                self.cr.lock_status()

        locks = [q['sql'] for q in queries.captured_queries if 'FOR ' in q['sql']]
        self.assertEqual(len(locks), 1)
        self.assertIn('FOR NO KEY UPDATE', locks[0])

    def test_a_deleted_request_is_not_locked(self):
        stale = self._stale()
        ChangeRequest.objects.filter(pk=self.cr.pk).delete()

        with transaction.atomic():
            self.assertFalse(stale.lock_status())


class ConcurrentTransitionTest(TransactionTestCase):
    """
    Real concurrent transactions. Each thread has its own database connection.
    """

    def setUp(self):
        group = Group.objects.create(name='Engineers')
        self.alice = User.objects.create(username='alice')
        self.bob = User.objects.create(username='bob')
        for user in (self.alice, self.bob):
            user.groups.add(group)
        requester = User.objects.create(username='requester')
        governing, _rule = make_policy(groups=[group], checks=list(BUILTIN_CHECKS))
        branch = Branch.objects.create(name=f'race-{self._testMethodName}'[:100])
        self.cr = ChangeRequest.objects.create(branch=branch, title='T', requester=requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=governing)
        self.cr.submit()
        self.errors = []

    def _run(self, *targets):
        """
        Run each target in its own thread and connection, and return the events dispatched.
        """

        def wrap(target):
            def run():
                try:
                    target()
                except Exception as e:
                    self.errors.append(repr(e))
                finally:
                    connection.close()

            return run

        with patch('extras.events.flush_events') as flush:
            threads = [threading.Thread(target=wrap(target)) for target in targets]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)

        self.assertEqual(self.errors, [])
        self.cr.refresh_from_db()
        return _emitted(flush)

    @staticmethod
    def _meet(barrier):
        try:
            barrier.wait(WAIT)
        except threading.BrokenBarrierError:
            pass

    def _approve(self, user, outer_transaction=False):
        def run():
            if outer_transaction:
                with transaction.atomic():
                    Review.objects.create(change_request_id=self.cr.pk, reviewer=user, decision='approve')
            else:
                Review.objects.create(change_request_id=self.cr.pk, reviewer=user, decision='approve')

        return run

    def test_two_approvals_at_once_announce_one_transition(self):
        barrier = threading.Barrier(2)
        evaluate = policy.evaluate_change_request

        def evaluate_together(change_request):
            self._meet(barrier)
            return evaluate(change_request)

        with patch.object(policy, 'evaluate_change_request', evaluate_together):
            events = self._run(self._approve(self.alice), self._approve(self.bob))

        self.assertEqual(events.count(CHANGE_REQUEST_APPROVED), 1)

    def test_two_approvals_inside_api_transactions_announce_one_transition(self):
        """
        NetBox runs an API write inside a transaction, so both reviews are inserted, and still
        uncommitted, before either refresh starts.
        """
        barrier = threading.Barrier(2)
        refresh = signal_receivers._refresh

        def refresh_together(change_request_id):
            self._meet(barrier)
            return refresh(change_request_id)

        with patch.object(signal_receivers, '_refresh', refresh_together):
            events = self._run(
                self._approve(self.alice, outer_transaction=True),
                self._approve(self.bob, outer_transaction=True),
            )

        self.assertEqual(events.count(CHANGE_REQUEST_APPROVED), 1)

    def test_an_approval_cannot_revive_a_request_abandoned_meanwhile(self):
        evaluating = threading.Event()
        abandoned = threading.Event()
        evaluate = policy.evaluate_change_request

        def evaluate_after_the_abandon(change_request):
            evaluating.set()
            abandoned.wait(WAIT)
            return evaluate(change_request)

        def abandon():
            evaluating.wait(WAIT)
            ChangeRequest.objects.get(pk=self.cr.pk).abandon()
            abandoned.set()

        with patch.object(policy, 'evaluate_change_request', evaluate_after_the_abandon):
            self._run(self._approve(self.alice), abandon)

        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.ABANDONED)

    def test_a_double_submit_announces_one_submission(self):
        ChangeRequest.objects.filter(pk=self.cr.pk).update(status=ChangeRequestStatusChoices.DRAFT)
        barrier = threading.Barrier(2)

        def submit():
            change_request = ChangeRequest.objects.get(pk=self.cr.pk)
            self._meet(barrier)
            change_request.submit()

        events = self._run(submit, submit)

        self.assertEqual(events.count(CHANGE_REQUEST_REVIEW_REQUESTED), 1)

    def test_a_comment_commits_while_a_transition_holds_the_lock(self):
        holding = threading.Event()
        evaluate = policy.evaluate_change_request
        took = {}

        def evaluate_slowly(change_request):
            holding.set()
            time.sleep(WAIT)
            return evaluate(change_request)

        def comment():
            holding.wait(WAIT)
            start = time.monotonic()
            with transaction.atomic():
                ChangeComment.objects.create(change_request_id=self.cr.pk, author=self.bob, text='hi')
            took['seconds'] = time.monotonic() - start

        with patch.object(policy, 'evaluate_change_request', evaluate_slowly):
            self._run(self._approve(self.alice), comment)

        self.assertLess(took['seconds'], WAIT / 2)
