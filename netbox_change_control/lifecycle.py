"""
The status transitions of a change request.

Every move of `status` is made here. Each one locks the change request row, decides from the
status read under that lock, writes, and then tells the reviewers and the event rules. When
the moves were spread across the model, the policy module, a signal receiver and the views,
one transition was announced twice and a stale one could undo a merge; one home is what keeps
each move decided once and announced once.

Two kinds of move:

- **Manual**: submit, return to draft, abandon, reopen. A person makes them, behind a permission.
- **Derived**: `refresh_status` recomputes Needs review, Approved or Rejected from the policy
  evaluation, and `complete` records a merge. Signals call them, so no caller can forget.
"""

from django.db import transaction

from netbox_change_control import events, policy
from netbox_change_control.choices import ChangeRequestStatusChoices

__all__ = (
    'abandon',
    'complete',
    'refresh_status',
    'reopen',
    'return_to_draft',
    'submit',
)

# The event each status announces on entry; Draft and Abandoned announce nothing.
STATUS_EVENTS = {
    ChangeRequestStatusChoices.NEEDS_REVIEW: events.CHANGE_REQUEST_REVIEW_REQUESTED,
    ChangeRequestStatusChoices.APPROVED: events.CHANGE_REQUEST_APPROVED,
    ChangeRequestStatusChoices.REJECTED: events.CHANGE_REQUEST_REJECTED,
    ChangeRequestStatusChoices.COMPLETED: events.CHANGE_REQUEST_COMPLETED,
}


def _announce(change_request, status, evaluation=None):
    """
    Tell the reviewers and the event rules that the request entered `status`.
    """
    from netbox_change_control.notifications import notify_status_change

    notify_status_change(change_request, status, evaluation)
    if event_type := STATUS_EVENTS.get(status):
        events.emit(change_request, event_type)


def _move(change_request, status):
    change_request.status = status
    change_request.save(update_fields=['status'])


def submit(change_request):
    """
    Put a draft into review, matching its policies. Returns True if the status moved.

    The status is set here rather than left to `refresh_status`, because that treats draft as
    the author's to hold and will not move a request out of it on its own. The announcement is
    made here for the same reason: by the time the refresh runs the status is already Needs
    review, so it sees no transition, and submitting without telling anybody is the one outcome
    that makes the whole thing pointless.
    """
    from netbox_change_control.batching import batched, schedule_refresh

    with transaction.atomic():
        if not change_request.lock_status() or not change_request.can_be_submitted:
            return False

        _move(change_request, ChangeRequestStatusChoices.NEEDS_REVIEW)
        with batched():
            policy.sync_policies(change_request)
            schedule_refresh(change_request)

        # The refresh may have carried it further, to Approved on a policy needing no human or
        # to Rejected on a standing rejection, and it announces those itself.
        change_request.refresh_from_db()
        if change_request.status == ChangeRequestStatusChoices.NEEDS_REVIEW:
            _announce(change_request, ChangeRequestStatusChoices.NEEDS_REVIEW)

        # Distinct from review requested, which also fires when a later edit invalidates an
        # approval: this one is the author's deliberate act of asking for review.
        events.emit(change_request, events.CHANGE_REQUEST_SUBMITTED)
    return True


def return_to_draft(change_request):
    """
    Pull a submitted request back out of review. Returns True if the status moved.

    The reviews are kept. They may already be stale, and if they are not they still stand: a
    reviewer's position on the change does not stop being their position because the author
    wants to work on it some more. What stops is the merge, because a draft is not approved and
    the gate reads the status.
    """
    with transaction.atomic():
        if not change_request.lock_status() or not change_request.can_return_to_draft:
            return False
        _move(change_request, ChangeRequestStatusChoices.DRAFT)
        policy.refresh_cached_state(change_request)
    return True


def abandon(change_request):
    """
    Give up on a change request. Returns True if the status moved.
    """
    with transaction.atomic():
        if not change_request.lock_status() or not change_request.can_be_abandoned:
            return False
        _move(change_request, ChangeRequestStatusChoices.ABANDONED)
    return True


def reopen(change_request):
    """
    Bring an abandoned request back, then let the evaluation decide where it lands.

    It returns to Draft rather than to whatever it held before, because the reviews it carried
    may since have gone stale and the policies may since have changed. Refreshing computes the
    honest answer instead of restoring a remembered one.
    """
    with transaction.atomic():
        if not change_request.lock_status() or not change_request.can_be_reopened:
            return False
        _move(change_request, ChangeRequestStatusChoices.DRAFT)
        if not change_request.branch_deleted:
            policy.sync_policies(change_request)
        refresh_status(change_request)
    return True


def complete(change_request):
    """
    Record that the branch has merged. Returns True if the status moved.
    """
    with transaction.atomic():
        if not change_request.lock_status():
            return False
        if change_request.status == ChangeRequestStatusChoices.COMPLETED:
            return False
        _move(change_request, ChangeRequestStatusChoices.COMPLETED)
        # Completion never passes through the refresh, so the cached columns the list shows
        # would otherwise keep reporting a merged request as ready to merge.
        policy.refresh_cached_state(change_request)
        _announce(change_request, ChangeRequestStatusChoices.COMPLETED)
    return True


def refresh_status(change_request, run_checks_on_approval=True):
    """
    Recompute a change request's status from its current policy evaluation.

    `status` is a cached view of the evaluation, so it must be refreshed on every path that can
    change the outcome: a review added, edited or removed, or a policy attached or detached.
    Signal receivers call this so no caller can forget.

    Two statuses are the author's to hold rather than the evaluation's to compute, and are left
    alone here. Terminal statuses are never reopened. Draft means "not submitted", so a request
    the author has pulled back must stay pulled back even while reviews arrive and policies move
    around it; without this, the first signal after a withdrawal would push it straight back
    into review. Submitting is what leaves draft, and it says so explicitly.

    `run_checks_on_approval` exists for the one caller which runs the checks itself immediately
    afterwards. Reaching Approved normally has to refresh them here, but a caller that is about
    to do it anyway would otherwise put the whole suite through twice.
    """
    with transaction.atomic():
        if not change_request.lock_status():
            return change_request.status
        return _refresh_locked_status(change_request, run_checks_on_approval)


def _refresh_locked_status(change_request, run_checks_on_approval):
    if change_request.status in ChangeRequestStatusChoices.TERMINAL:
        return change_request.status

    if change_request.status == ChangeRequestStatusChoices.DRAFT:
        # The cached columns still have to follow the branch: a draft can gain a conflict.
        policy.refresh_cached_state(change_request)
        return change_request.status

    evaluation = policy.evaluate_change_request(change_request)
    if evaluation.rejections:
        status = ChangeRequestStatusChoices.REJECTED
    elif evaluation.satisfied:
        status = ChangeRequestStatusChoices.APPROVED
    else:
        status = ChangeRequestStatusChoices.NEEDS_REVIEW

    if status != change_request.status:
        _move(change_request, status)
        # Announced only on an actual transition, so a busy request does not spam reviewers or
        # fire a webhook per save.
        _announce(change_request, status, evaluation)

        if status == ChangeRequestStatusChoices.APPROVED and run_checks_on_approval:
            # Reaching Approved is the moment a merge becomes possible, so refresh the checks
            # here. A branch edit invalidates the reviews but not the stored check results, so
            # without this a request could be edited to introduce a conflict, re-approved, and
            # then merged against a stale "no conflicts" row. run_checks also attempts the
            # automatic merge and refreshes the cached columns, so neither is repeated here.
            from netbox_change_control.checks import run_checks

            run_checks(change_request)
            return status

    # Last, so it reads the settled status.
    policy.refresh_cached_state(change_request)
    return status
