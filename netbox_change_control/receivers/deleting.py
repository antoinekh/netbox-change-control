"""
Which change requests are being deleted, so the other receivers can leave them alone.
"""

from contextvars import ContextVar

from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from netbox_change_control.models import ChangeRequest

__all__ = (
    'is_being_deleted',
    'mark_change_request_deleting',
    'unmark_change_request_deleting',
)


# Change requests whose deletion is under way.
#
# Deleting a change request cascades to its reviews, comments and checks, and the other
# receivers react to each of those by refreshing the request. Refreshing re-creates the check
# rows, which are then inserted against a change request row that is about to disappear, and
# the whole delete fails on commit with a foreign key violation. Deleting a change request
# that carried a comment thread was a server error because of it.
#
# Django's collector sends every pre_delete before any post_delete, so marking the request
# here is enough to silence the receivers for the rest of the cascade.
_deleting = ContextVar('netbox_change_control.deleting_change_requests', default=frozenset())


def is_being_deleted(change_request_id):
    """
    True while this change request is inside its own delete cascade.

    A rolled-back delete leaves its id marked until the context ends. The only consequence is
    a skipped status refresh, which the next write to that request repairs.
    """
    return change_request_id in _deleting.get()


@receiver(pre_delete, sender=ChangeRequest)
def mark_change_request_deleting(sender, instance, **kwargs):
    _deleting.set(_deleting.get() | {instance.pk})


@receiver(post_delete, sender=ChangeRequest)
def unmark_change_request_deleting(sender, instance, **kwargs):
    _deleting.set(_deleting.get() - {instance.pk})
