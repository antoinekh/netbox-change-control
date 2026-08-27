"""
Telling a real conflict from a stale baseline.

netbox-branching flags a conflict by comparing a ChangeDiff's `modified` and `current`
against `original`. `original` is written once, when the diff is first created, and is never
advanced -- not by a sync, not by anything. So once main has touched a field, every later
edit to that field in the branch is flagged as a conflict, even when the branch has since
synced and is fully up to date with main.

In git terms that is a fast-forward, not a conflict, and forcing a reviewer to acknowledge it
teaches them to acknowledge conflicts by reflex -- which is exactly what must not happen for
a real one.

A conflict is real only when main has changed the object **since the branch last synced**. If
main has not moved since the sync, the branch already contains main's value and merging
cannot discard anything.
"""

__all__ = (
    'conflicting_diffs',
    'stale_baseline_diffs',
)


def _split(branch):
    """
    Return (real, stale) lists of conflicted ChangeDiffs for the branch.
    """
    from netbox_branching.models import ChangeDiff

    conflicted = list(ChangeDiff.objects.filter(branch=branch, conflicts__isnull=False).select_related('object_type'))
    if not conflicted:
        return [], []

    # Objects main has actually changed since the last sync. Anything outside this set is
    # flagged only because the baseline was never advanced.
    moved_in_main = set(branch.get_unsynced_changes().values_list('changed_object_type_id', 'changed_object_id'))

    real, stale = [], []
    for diff in conflicted:
        key = (diff.object_type_id, diff.object_id)
        (real if key in moved_in_main else stale).append(diff)
    return real, stale


def conflicting_diffs(branch):
    """
    Conflicts where main has moved since the last sync, so a merge would discard work.
    """
    if branch is None:
        return []
    return _split(branch)[0]


def stale_baseline_diffs(branch):
    """
    Diffs flagged by branching which a sync has already reconciled.
    """
    if branch is None:
        return []
    return _split(branch)[1]
