"""
Reviewer notifications.

NetBox already has a per-user notification inbox, so this only decides who to tell and when.
Notifications are raised on a status transition, never on every save, so a busy change
request does not spam its reviewers.
"""

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from netbox.plugins import get_plugin_config

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.events import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_REJECTED,
    CHANGE_REQUEST_REVERTED,
    CHANGE_REQUEST_REVIEW_REQUESTED,
    CHANGE_REQUEST_WINDOW_CLOSED,
)

__all__ = (
    'notify_revert',
    'notify_status_change',
    'notify_window_closed',
    'pending_reviewers',
)


def pending_reviewers(change_request, evaluation=None):
    """
    Return the users who could still move this change request forward.

    Only rules which are not yet satisfied are considered, so a reviewer is not pestered
    about a rule their colleagues have already met. The requester is always excluded, since
    they may not review their own request.
    """
    evaluation = evaluation or change_request.evaluate()

    user_ids = set()
    for rule in evaluation.rules:
        if rule.satisfied:
            continue
        user_ids.update(rule.rule.eligible_users().values_list('pk', flat=True))

    user_ids.discard(change_request.requester_id)

    from users.models import User

    return User.objects.filter(pk__in=user_ids)


def notify_status_change(change_request, status, evaluation=None):
    """
    Raise notifications for a status transition. Called only when the status actually
    changed.
    """
    if not get_plugin_config('netbox_change_control', 'notify_reviewers'):
        return

    if status == ChangeRequestStatusChoices.NEEDS_REVIEW:
        recipients = pending_reviewers(change_request, evaluation)
        event_type = CHANGE_REQUEST_REVIEW_REQUESTED
    elif status == ChangeRequestStatusChoices.APPROVED:
        # The requester is the one who acts next, by merging.
        recipients = [change_request.requester]
        event_type = CHANGE_REQUEST_APPROVED
    elif status == ChangeRequestStatusChoices.REJECTED:
        recipients = [change_request.requester]
        event_type = CHANGE_REQUEST_REJECTED
    else:
        return

    _deliver(change_request, recipients, event_type)


def notify_revert(change_request):
    """
    Tell the requester that the merge was reverted, since they act next by submitting again.
    """
    if not get_plugin_config('netbox_change_control', 'notify_reviewers'):
        return

    _deliver(change_request, [change_request.requester], CHANGE_REQUEST_REVERTED)


def notify_window_closed(change_request):
    """
    Tell the requester that the change window closed before the automatic merge, since they act
    next by setting a new window or merging by hand.
    """
    if not get_plugin_config('netbox_change_control', 'notify_reviewers'):
        return

    _deliver(change_request, [change_request.requester], CHANGE_REQUEST_WINDOW_CLOSED)


def _deliver(change_request, recipients, event_type):
    """
    Write one notification per recipient.

    Notification carries a unique constraint on (object_type, object_id, user), so a repeat
    notification updates the existing row, marks it unread again and dates it now, so it sorts
    with the new notifications rather than at its first date.
    """
    from extras.models import Notification

    object_type = ContentType.objects.get_for_model(change_request)
    for user in recipients:
        Notification.objects.update_or_create(
            object_type=object_type,
            object_id=change_request.pk,
            user=user,
            defaults={
                'event_type': event_type,
                'object_repr': str(change_request)[:200],
                'read': None,
                'created': timezone.now(),
            },
        )
