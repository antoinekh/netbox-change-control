"""
Lifecycle events reaching NetBox's event pipeline.

Registering an event type only makes it selectable on an event rule. Nothing fires unless an
event carrying that type is put through the pipeline, so a rule on an unemitted type is
silently inert. These pin that each transition emits, that it emits once, and that it does
not disturb the core object events for the same object.
"""

from unittest.mock import patch

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.events import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_REJECTED,
    CHANGE_REQUEST_REVIEW_REQUESTED,
)
from netbox_change_control.models import Review
from netbox_change_control.tests.base import ChangeControlTestCase


class LifecycleEventTest(ChangeControlTestCase):
    branch_prefix = 'evt'

    def _emitted(self, action):
        """
        Run `action` and return the event types dispatched to the pipeline.

        Dispatch is deferred to commit, so the callbacks are captured rather than waited for.
        """
        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                action()
        return [event['event_type'] for call in flush.call_args_list for event in call.args[0]]

    def test_reaching_approved_emits(self):
        events = self._emitted(lambda: self._approve())
        self.assertIn(CHANGE_REQUEST_APPROVED, events)

    def test_reaching_needs_review_emits(self):
        self._approve()
        review = self.cr.reviews.get()
        events = self._emitted(review.delete)
        self.assertIn(CHANGE_REQUEST_REVIEW_REQUESTED, events)

    def test_a_rejection_emits(self):
        def reject():
            Review.objects.create(
                change_request=self.cr,
                reviewer=self.reviewer,
                decision=ReviewDecisionChoices.REJECT,
                comment='no',
            )

        events = self._emitted(reject)
        self.assertIn(CHANGE_REQUEST_REJECTED, events)

    def test_a_write_which_does_not_move_the_status_emits_nothing(self):
        """
        refresh_status runs on many signals. Only a real transition is an event.
        """
        self._approve()
        events = self._emitted(lambda: self.cr.save())
        self.assertEqual(events, [])

    def test_the_event_carries_the_change_request(self):
        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                self._approve()

        events = [e for call in flush.call_args_list for e in call.args[0]]
        approved = [e for e in events if e['event_type'] == CHANGE_REQUEST_APPROVED]
        self.assertEqual(len(approved), 1)
        event = approved[0]
        self.assertEqual(event['object_id'], self.cr.pk)
        self.assertEqual(event['object'].pk, self.cr.pk)
        # The serializer renders a choice as {'value', 'label'} for a real request and as a
        # bare string without one, so accept either rather than pinning NetBox's rendering.
        status = event['data']['status']
        self.assertEqual(
            status['value'] if isinstance(status, dict) else status,
            ChangeRequestStatusChoices.APPROVED,
        )

    def test_the_core_object_queue_is_left_alone(self):
        """
        NetBox's queue holds one event per object per request. Enqueuing a lifecycle event
        there would either be swallowed by the `object_updated` the status write produces, or
        replace it and break every rule watching for an ordinary update.
        """
        from netbox.context import events_queue

        events_queue.set({})
        with self.captureOnCommitCallbacks(execute=True):
            self._approve()

        key = f'netbox_change_control.changerequest:{self.cr.pk}'
        queued = events_queue.get().get(key)
        if queued is not None:
            self.assertNotIn('change_request', queued['event_type'])

    def test_nothing_is_dispatched_if_the_transaction_rolls_back(self):
        """
        A webhook announcing an approval that never happened cannot be recalled.
        """
        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=False):
                self._approve()
            flush.assert_not_called()
