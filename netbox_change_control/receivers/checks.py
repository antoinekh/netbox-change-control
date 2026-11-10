"""
Keep the pre-merge check results current, and react when one changes.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from netbox_branching.models import ChangeDiff

from netbox_change_control.automerge import try_auto_merge
from netbox_change_control.checks import run_checks
from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
)
from netbox_change_control.policy import refresh_cached_state
from netbox_change_control.receivers.deleting import is_being_deleted

__all__ = (
    'react_to_check_result',
    'rerun_checks_on_comment_change',
    'rerun_checks_on_diff_change',
    'run_checks_for_new_request',
)


@receiver(post_save, sender=ChangeRequest)
def run_checks_for_new_request(sender, instance, created, **kwargs):
    """
    Run the checks as soon as a change request exists.

    Creating the rows without running them left every new request showing four checks stuck
    on "pending" until somebody pressed Re-run, which reads as broken and blocks the merge
    on checks nobody was asked to run.
    """
    if created:
        run_checks(instance)


@receiver([post_save, post_delete], sender=ChangeComment)
def rerun_checks_on_comment_change(sender, instance, **kwargs):
    """
    Re-run the checks when a comment thread is opened, resolved or removed.

    The `threads-resolved` built-in reads the comment threads, so without this its result
    would go stale the moment somebody resolved a thread.
    """
    if is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return
    run_checks(change_request)


@receiver(post_save, sender=MergeCheck)
def react_to_check_result(sender, instance, **kwargs):
    """
    A check result moves whether the request is ready, and can be the last gate it was
    waiting on.

    Both halves matter, and they are not the same condition. The cached readiness the change
    request list shows has to follow a result in either direction: a pipeline reporting
    success over the REST API is the common way the last gate clears, and it reaches this
    plugin as a plain save with no other signal behind it. Auto-merge only cares about a
    result that passes.
    """
    if is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return

    refresh_cached_state(change_request)

    if instance.is_passing:
        try_auto_merge(change_request)


@receiver(post_save, sender=ChangeDiff)
def rerun_checks_on_diff_change(sender, instance, **kwargs):
    """
    Re-run the checks when a branch diff changes and that contradicts the stored result.

    A conflict can appear with no event on this plugin at all: somebody edits the same field
    in main, and branching recomputes the diff. The stored `no-conflicts` result would stay
    green, and the merge gate reads stored results.

    The guard keeps this cheap. ChangeDiff is written for every changed object, so a bulk
    edit fires this many times; comparing the live conflict state against the stored result
    first means the common case costs two indexed queries and no writes.
    """
    change_request = ChangeRequest.objects.filter(
        branch_id=instance.branch_id,
        status__in=ChangeRequestStatusChoices.OPEN,
    ).first()
    if change_request is None or is_being_deleted(change_request.pk):
        return

    # A clean diff on a request nothing has ever flagged cannot change the answer. Another
    # diff turning conflicted arrives as its own save, so it is caught there. This matters:
    # a bulk edit inside a branch writes one ChangeDiff per object, and without this guard
    # every one of them pays for the conflict test below.
    if not instance.conflicts and not change_request.cached_conflicted:
        return

    conflicted = ChangeDiff.objects.filter(branch_id=instance.branch_id, conflicts__isnull=False).exists()

    # The change request list reads a cached conflict flag, and it has to follow the diff
    # whether or not any policy asked for the no-conflicts check. Only that check creates the
    # row consulted below, so a request governed by a policy which does not require it had no
    # path back to the cache at all.
    if conflicted != change_request.cached_conflicted:
        ChangeRequest.objects.filter(pk=change_request.pk).update(cached_conflicted=conflicted)

    stored = change_request.checks.filter(name='no-conflicts').first()
    if stored is None:
        return
    if conflicted == (stored.status != MergeCheckStatusChoices.SUCCESS):
        # The stored result already agrees with reality.
        return

    run_checks(change_request)
