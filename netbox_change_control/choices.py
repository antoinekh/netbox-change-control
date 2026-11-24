from enum import StrEnum

from django.utils.translation import gettext_lazy as _
from utilities.choices import ChoiceSet

__all__ = (
    'ChangeRequestPriorityChoices',
    'ChangeRequestStatusChoices',
    'ChangeWindowState',
    'ConditionStateChoices',
    'HistoryActionChoices',
    'MergeCheckStatusChoices',
    'ReviewDecisionChoices',
)


class ChangeRequestStatusChoices(ChoiceSet):
    """
    Lifecycle of a change request. Only APPROVED opens the merge gate.
    """

    DRAFT = 'draft'
    NEEDS_REVIEW = 'needs-review'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    COMPLETED = 'completed'
    ABANDONED = 'abandoned'

    CHOICES = (
        (DRAFT, _('Draft'), 'gray'),
        (NEEDS_REVIEW, _('Needs review'), 'orange'),
        (APPROVED, _('Approved'), 'green'),
        (REJECTED, _('Rejected'), 'red'),
        (COMPLETED, _('Completed'), 'blue'),
        (ABANDONED, _('Abandoned'), 'gray'),
    )

    # Statuses which mean the request is finished and must not be reopened.
    TERMINAL = (COMPLETED, ABANDONED)

    # Statuses whose policy evaluation is still meaningful.
    OPEN = (DRAFT, NEEDS_REVIEW, APPROVED, REJECTED)

    # Submitted and still open: the statuses the evaluation computes.
    UNDER_REVIEW = (NEEDS_REVIEW, APPROVED, REJECTED)


class ChangeWindowState(StrEnum):
    """
    Where the clock stands against a change request's window.
    """

    NONE = 'none'
    EARLY = 'early'
    OPEN = 'open'
    CLOSED = 'closed'


class ChangeRequestPriorityChoices(ChoiceSet):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'

    CHOICES = (
        (LOW, _('Low'), 'green'),
        (MEDIUM, _('Medium'), 'blue'),
        (HIGH, _('High'), 'orange'),
        (CRITICAL, _('Critical'), 'red'),
    )


class ConditionStateChoices(ChoiceSet):
    """
    Which state a policy's conditions are evaluated against.

    A change has two sides: the object before it and the object after. `status == active`
    read only against the second means "leaves it active", which misses a live circuit being
    decommissioned, the change most in need of review. EITHER is the default because a policy
    exists to catch changes, and the safe failure is to attach and ask for a review.

    This applies to plain attribute names only. A condition using `changed`, `unchanged` or a
    `snapshots.` path reads both sides itself, so it answers the same whichever is chosen.
    """

    EITHER = 'either'
    AFTER = 'after'
    BEFORE = 'before'

    CHOICES = (
        (EITHER, _('Either side of the change')),
        (AFTER, _('After the change')),
        (BEFORE, _('Before the change')),
    )


class HistoryActionChoices(ChoiceSet):
    """
    What happened to a change request: each status change, and each merge or revert of its branch.
    """

    SUBMITTED = 'submitted'
    NEEDS_REVIEW = 'needs-review'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    RETURNED_TO_DRAFT = 'returned-to-draft'
    MERGED = 'merged'
    REVERTED = 'reverted'
    ABANDONED = 'abandoned'
    REOPENED = 'reopened'

    CHOICES = (
        (SUBMITTED, _('Submitted'), 'cyan'),
        (NEEDS_REVIEW, _('Needs review'), 'orange'),
        (APPROVED, _('Approved'), 'green'),
        (REJECTED, _('Rejected'), 'red'),
        (RETURNED_TO_DRAFT, _('Returned to draft'), 'gray'),
        (MERGED, _('Merged'), 'blue'),
        (REVERTED, _('Reverted'), 'purple'),
        (ABANDONED, _('Abandoned'), 'gray'),
        (REOPENED, _('Reopened'), 'teal'),
    )


class ReviewDecisionChoices(ChoiceSet):
    APPROVE = 'approve'
    REJECT = 'reject'
    COMMENT = 'comment'

    CHOICES = (
        (APPROVE, _('Approve'), 'green'),
        (REJECT, _('Request changes'), 'red'),
        (COMMENT, _('Comment'), 'gray'),
    )


class MergeCheckStatusChoices(ChoiceSet):
    """
    Outcome of a pre-merge check. Only SUCCESS and SKIPPED clear the gate.
    """

    PENDING = 'pending'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILURE = 'failure'
    ERROR = 'error'
    SKIPPED = 'skipped'

    CHOICES = (
        (PENDING, _('Pending'), 'gray'),
        (RUNNING, _('Running'), 'cyan'),
        (SUCCESS, _('Passed'), 'green'),
        (FAILURE, _('Failed'), 'red'),
        (ERROR, _('Errored'), 'orange'),
        (SKIPPED, _('Skipped'), 'gray'),
    )

    # A required check in any other state blocks the merge.
    PASSING = (SUCCESS, SKIPPED)
