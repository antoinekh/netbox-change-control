"""
Lifecycle events reaching NetBox's event pipeline.

Registering an event type only makes it selectable on an event rule. Nothing fires unless an
event carrying that type is put through the pipeline, so a rule on an unemitted type is
silently inert. These pin that each transition emits, that it emits once, and that it does
not disturb the core object events for the same object.
"""

from unittest.mock import patch

from django.test import TestCase

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.events import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_COMPLETED,
    CHANGE_REQUEST_REJECTED,
    CHANGE_REQUEST_REVIEW_REQUESTED,
    CHANGE_REQUEST_SUBMITTED,
    REVIEW_SUBMITTED,
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


class EventRuleFormChoicesTest(TestCase):
    """
    Every registered event type is offered by NetBox's event rule forms.

    A type the forms do not offer cannot be picked in the interface, and a CSV import naming it
    is refused, even though it is registered and the REST API accepts it. NetBox 4.7 imports
    these forms before any plugin is ready, so a list read at import misses every plugin type.
    """

    OUR_EVENTS = (
        CHANGE_REQUEST_SUBMITTED,
        CHANGE_REQUEST_REVIEW_REQUESTED,
        REVIEW_SUBMITTED,
        CHANGE_REQUEST_APPROVED,
        CHANGE_REQUEST_REJECTED,
        CHANGE_REQUEST_COMPLETED,
    )

    @staticmethod
    def _forms():
        from extras.forms import EventRuleBulkEditForm, EventRuleForm, EventRuleImportForm

        return EventRuleForm, EventRuleBulkEditForm, EventRuleImportForm

    @staticmethod
    def _offered(form_class):
        return {value for value, _label in form_class().fields['event_types'].choices}

    def test_our_event_types_are_offered(self):
        for form_class in self._forms():
            offered = self._offered(form_class)
            for name in self.OUR_EVENTS:
                with self.subTest(form=form_class.__name__, event=name):
                    self.assertIn(name, offered)

    def test_every_registered_event_type_is_offered(self):
        """
        Plugins listed after this one, netbox-branching among them, register theirs later still.
        """
        from netbox.registry import registry

        for form_class in self._forms():
            with self.subTest(form=form_class.__name__):
                self.assertEqual(set(registry['event_types']) - self._offered(form_class), set())

    def test_csv_import_accepts_our_event_type(self):
        from extras.forms import EventRuleImportForm

        field = EventRuleImportForm().fields['event_types']
        self.assertEqual(field.clean(CHANGE_REQUEST_APPROVED), [CHANGE_REQUEST_APPROVED])
