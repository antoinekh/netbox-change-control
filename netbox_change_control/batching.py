"""
Collapsing repeated work on one change request.

Refreshing a change request means recomputing its status and running its checks. Both are
correct to do on every event that could change the answer, and doing them per event is how the
plugin stays consistent without anybody remembering to call anything.

The cost is that one user action is often many events. Submitting a request attaches every
matching policy, and each binding is its own signal, so a request governed by three policies
refreshed three times and ran every check three times before the view had even returned. The
answer was identical each time.

This is the fix, and it is deliberately not `transaction.on_commit`. Deferring past the commit
would mean a request's checks are not yet run when the view that submitted it renders the next
page, and it would leave every test that asserts on a check result depending on
`captureOnCommitCallbacks`. Instead the work is collapsed within an explicit block: a caller
that knows it is about to cause a burst of events wraps it, and the refresh runs once per
change request when the block ends.

Outside such a block `schedule_refresh` runs immediately, so nothing changes for the many
callers which are not part of a burst.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

__all__ = (
    'batched',
    'schedule_refresh',
)

logger = logging.getLogger('netbox.plugins.netbox_change_control.batching')

# The change request ids waiting to be refreshed, or None when not inside a block.
#
# None and an empty set mean different things here: None is "refresh as you go", an empty set
# is "a block is open and nothing has asked yet". The two must not be conflated, or a block
# that happens to collect nothing would start running work immediately.
_pending = ContextVar('netbox_change_control.pending_refreshes', default=None)


@contextmanager
def batched():
    """
    Collapse the refreshes caused inside this block into one per change request.

    Nesting is safe: only the outermost block flushes, so a caller can wrap an operation
    without knowing whether its own caller already did.

    Nothing is flushed if the block raises. The work would be computed against a state that is
    about to roll back, and the exception is the caller's to handle rather than something to
    bury under a burst of check runs.
    """
    if _pending.get() is not None:
        # Already inside a block. The outermost one owns the flush.
        yield
        return

    token = _pending.set(set())
    try:
        yield
    except Exception:
        _pending.reset(token)
        raise
    else:
        pending = _pending.get()
        _pending.reset(token)
        for change_request_id in sorted(pending):
            _refresh(change_request_id)


def schedule_refresh(change_request):
    """
    Refresh this change request's status and checks, now or at the end of the current block.
    """
    if (pending := _pending.get()) is None:
        _refresh(change_request.pk)
    else:
        pending.add(change_request.pk)


def _refresh(change_request_id):
    """
    Recompute one change request's status, then run its checks.

    Status first, because `run_checks` may auto-merge and should decide against a current
    status. `refresh_status` is told not to run the checks itself on the way to Approved,
    since they are run here unconditionally a moment later; letting it would put the whole
    check suite through twice for the one transition that matters most.
    """
    from netbox_change_control.checks import run_checks
    from netbox_change_control.models import ChangeRequest
    from netbox_change_control.policy import refresh_status

    change_request = ChangeRequest.objects.filter(pk=change_request_id).first()
    if change_request is None:
        return

    refresh_status(change_request, run_checks_on_approval=False)
    change_request.refresh_from_db()
    run_checks(change_request)
