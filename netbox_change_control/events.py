"""
Event types, so a change request can drive NetBox notifications and event rules.

Two halves. Registering a type makes it selectable on an event rule; `emit` is what actually
puts an event of that type through the pipeline so the rule fires.

The text is translated eagerly with `gettext`, not `gettext_lazy`, which is what NetBox does
for its own event types. `EventType.__str__` returns the text unchanged, so a lazy proxy
makes `str(event_type)` raise TypeError. The notification list renders `{{ notification.event }}`,
so one lazy event type took out the whole page for every user holding such a notification:
the bell showed a count and opening it returned a 500.
"""

from django.utils.translation import gettext as _
from netbox.events import EVENT_TYPE_KIND_INFO, EVENT_TYPE_KIND_SUCCESS, EVENT_TYPE_KIND_WARNING, EventType

__all__ = (
    'CHANGE_REQUEST_APPROVED',
    'CHANGE_REQUEST_COMPLETED',
    'CHANGE_REQUEST_REJECTED',
    'CHANGE_REQUEST_REVIEW_REQUESTED',
    'CHANGE_REQUEST_SUBMITTED',
    'REVIEW_SUBMITTED',
    'emit',
)

CHANGE_REQUEST_SUBMITTED = 'change_request_submitted'
CHANGE_REQUEST_REVIEW_REQUESTED = 'change_request_review_requested'
REVIEW_SUBMITTED = 'review_submitted'
CHANGE_REQUEST_APPROVED = 'change_request_approved'
CHANGE_REQUEST_REJECTED = 'change_request_rejected'
CHANGE_REQUEST_COMPLETED = 'change_request_completed'

EventType(CHANGE_REQUEST_SUBMITTED, _('Change request submitted'), kind=EVENT_TYPE_KIND_INFO).register()
EventType(CHANGE_REQUEST_REVIEW_REQUESTED, _('Change request needs review'), kind=EVENT_TYPE_KIND_INFO).register()
EventType(REVIEW_SUBMITTED, _('Review submitted'), kind=EVENT_TYPE_KIND_INFO).register()
EventType(CHANGE_REQUEST_APPROVED, _('Change request approved'), kind=EVENT_TYPE_KIND_SUCCESS).register()
EventType(CHANGE_REQUEST_REJECTED, _('Change request rejected'), kind=EVENT_TYPE_KIND_WARNING).register()
EventType(CHANGE_REQUEST_COMPLETED, _('Change request completed'), kind=EVENT_TYPE_KIND_SUCCESS).register()


def emit(change_request, event_type):
    """
    Push a lifecycle event into NetBox's event pipeline, so an event rule can fire a webhook
    or run a script on it. Returns True if the event was dispatched.

    Registering an event type is only half the job: it makes the type selectable on an event
    rule, but nothing fires unless an event carrying that type reaches the pipeline. A rule
    on an unemitted type is silently inert, which is worse than not offering the type at all.

    This deliberately does not use `enqueue_event`. That queue is keyed by object, one event
    per object per request, so a lifecycle event would either be swallowed by the
    `object_updated` event that the status write already queued, or replace it and break
    every rule watching for an ordinary update. A lifecycle event is a second, independent
    fact about the same object, so it is flushed on its own.

    Dispatch waits for the commit. A webhook announcing an approval that then rolled back
    cannot be recalled.
    """
    from core.models import ObjectType
    from django.db import transaction
    from extras.events import EventContext, flush_events, get_snapshots
    from netbox.context import current_request

    request = current_request.get()

    event = EventContext(
        object_type=ObjectType.objects.get_for_model(change_request),
        object_id=change_request.pk,
        object=change_request,
        event_type=event_type,
        snapshots=get_snapshots(change_request, event_type),
        user=getattr(request, 'user', None),
    )
    # Optional: a status change can come from a background job, and NetBox's own job events
    # reach the pipeline without a request too. The webhook context carries the user and the
    # request id under `request`; NetBox 4.7 removed the flat `username` and `request_id`
    # keys, so setting them here would put two dead entries on every event.
    if request is not None:
        event['request'] = request

    transaction.on_commit(lambda: flush_events([event]))
    return True
