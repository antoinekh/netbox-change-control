from django.utils.translation import gettext_lazy as _
from utilities.choices import ChoiceSet

__all__ = (
    'ChangeRequestPriorityChoices',
    'ChangeRequestStatusChoices',
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
