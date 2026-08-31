"""
Automatic merging.

A change request marked `auto_merge` is merged as soon as every gate is satisfied: the
policies are met, every required check passes, and the change window is open.

Two triggers, because a request can become mergeable either by something happening (a final
approval, a check reporting) or by time passing (the window opening):

- `try_auto_merge` runs on the events that can satisfy the last gate;
- `run_due_auto_merges` is a periodic job that catches windows opening.

The second trigger only fires as often as `auto_merge_interval`, so `unreliable_window`
reports the case where the window is too short for that sweep to be relied on.
"""

import logging
from dataclasses import dataclass

from core.choices import JobStatusChoices
from netbox.plugins import get_plugin_config

from netbox_change_control.choices import ChangeRequestStatusChoices

__all__ = (
    'UnreliableWindow',
    'run_due_auto_merges',
    'sweep_interval_minutes',
    'try_auto_merge',
    'unreliable_window',
)

logger = logging.getLogger('netbox.plugins.netbox_change_control.automerge')


@dataclass(frozen=True)
class UnreliableWindow:
    """
    A change window the periodic sweep cannot be trusted to catch.
    """

    window_minutes: int
    interval_minutes: int


def sweep_interval_minutes():
    """
    Minutes between periodic sweeps, or None when the setting is unusable.

    A bad value is reported as None rather than raised, because this is read to render a
    warning. `netbox_change_control.jobs` validates the same setting at start-up, which is
    where a misconfiguration should be loud.
    """
    value = get_plugin_config('netbox_change_control', 'auto_merge_interval')
    if type(value) is not int or value < 1:
        return None
    return value


def unreliable_window(change_request):
    """
    Return an UnreliableWindow when automatic merging cannot be relied on, else None.

    Automatic merge has two triggers, and a short window can defeat both. The event trigger
    only fires when the last approval or the last check arrives while the window is already
    open; a request that is ready before the window opens depends entirely on the periodic
    sweep. That sweep runs every `auto_merge_interval` minutes, so a window shorter than the
    interval can fall between two runs and never be looked at.

    Only a window with both bounds can be too short. A window with one bound stays open
    indefinitely in one direction, so a sweep always finds it.
    """
    if not change_request.auto_merge:
        return None
    if not get_plugin_config('netbox_change_control', 'enable_auto_merge'):
        return None
    if not (change_request.scheduled_start and change_request.scheduled_end):
        return None

    interval = sweep_interval_minutes()
    if interval is None:
        return None

    window = int((change_request.scheduled_end - change_request.scheduled_start).total_seconds() // 60)
    if window >= interval:
        return None
    return UnreliableWindow(window_minutes=window, interval_minutes=interval)


def try_auto_merge(change_request):
    """
    Merge the request if it is eligible. Returns True if a merge was started.

    Every reason not to merge is checked here rather than relying on the merge gate to
    refuse, so a routine "not ready yet" never surfaces as an exception.
    """
    if not get_plugin_config('netbox_change_control', 'enable_auto_merge'):
        return False
    if not change_request.auto_merge:
        return False
    if change_request.status != ChangeRequestStatusChoices.APPROVED:
        return False
    if change_request.branch_deleted:
        return False
    if not change_request.window_is_open:
        return False

    branch = change_request.branch

    # Enqueue rather than merge inline. try_auto_merge is reached from a signal, so a direct
    # call would run a whole branch merge inside the web request that submitted the final
    # review, and would bypass the branching plugin's job_timeout handling. This is the same
    # path the branching plugin's own merge button takes.
    from netbox_branching.jobs import MergeBranchJob

    # One merge per branch, however many times this is reached.
    #
    # Nothing about becoming mergeable happens once. A single write can arrive here by more
    # than one route: refreshing the status runs the checks, which try to merge, and a caller
    # that then runs the checks itself tries again. The status is still Approved at the second
    # call, because the merge has only been queued and not yet run, so the second call used to
    # queue a duplicate. The first job merged and the second then failed with "not ready to
    # merge", which reads as a broken merge on a change that in fact went through.
    #
    # Guarding on the queue rather than on the call sites is what makes this hold for routes
    # nobody has thought of yet. This is the same test NetBox's own enqueue_once applies.
    if already := MergeBranchJob.get_jobs(branch).filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES).first():
        logger.debug('Auto-merge for %s already queued as job %s', change_request, already.pk)
        return False

    indicator = branch.can_merge
    if not indicator.permitted:
        logger.debug('Auto-merge skipped for %s: %s', change_request, indicator.message)
        return False

    logger.info('Enqueuing auto-merge for %s', change_request)
    MergeBranchJob.enqueue(
        instance=branch,
        user=change_request.requester,
        commit=True,
    )
    return True


def run_due_auto_merges():
    """
    Merge every request whose gates are now satisfied.

    Called on a schedule so a request waiting only on its window opening is picked up.
    """
    from netbox_change_control.models import ChangeRequest

    candidates = ChangeRequest.objects.filter(
        auto_merge=True,
        status=ChangeRequestStatusChoices.APPROVED,
        branch__isnull=False,
    ).select_related('branch', 'requester')

    merged = 0
    for change_request in candidates:
        try:
            if try_auto_merge(change_request):
                merged += 1
        except Exception:
            logger.exception('Auto-merge failed for %s', change_request)
    return merged
