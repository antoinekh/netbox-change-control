from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel, PrimaryModel

from netbox_change_control.choices import (
    ChangeRequestPriorityChoices,
    ChangeRequestStatusChoices,
    ReviewDecisionChoices,
)

__all__ = (
    'ChangeRequest',
    'ChangeRequestPolicy',
    'Review',
)


class ChangeRequest(PrimaryModel):
    """
    A proposal to merge one branch, together with the policies which govern it and the
    reviews submitted against it.
    """

    branch = models.OneToOneField(
        to='netbox_branching.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='change_request',
        verbose_name=_('branch'),
        help_text=_('Cleared if the branch is deleted. The record of the change survives.'),
    )
    branch_name = models.CharField(
        verbose_name=_('branch name'),
        max_length=100,
        blank=True,
        editable=False,
        help_text=_('Name of the branch, kept so the record stays readable after the branch is gone.'),
    )
    ref = models.CharField(
        verbose_name=_('reference'),
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_('External reference: a ticket id, a change number, whatever your process uses.'),
    )
    title = models.CharField(
        verbose_name=_('title'),
        max_length=200,
    )
    status = models.CharField(
        verbose_name=_('status'),
        max_length=32,
        choices=ChangeRequestStatusChoices,
        default=ChangeRequestStatusChoices.DRAFT,
    )
    priority = models.CharField(
        verbose_name=_('priority'),
        max_length=32,
        choices=ChangeRequestPriorityChoices,
        default=ChangeRequestPriorityChoices.MEDIUM,
    )
    requester = models.ForeignKey(
        to='users.User',
        on_delete=models.PROTECT,
        related_name='change_requests',
        verbose_name=_('requester'),
    )
    scheduled_start = models.DateTimeField(
        verbose_name=_('window opens'),
        null=True,
        blank=True,
        help_text=_('Earliest time this change may be merged. Leave empty for no restriction.'),
    )
    scheduled_end = models.DateTimeField(
        verbose_name=_('window closes'),
        null=True,
        blank=True,
        help_text=_('Latest time this change may be merged. Leave empty for no restriction.'),
    )
    auto_merge = models.BooleanField(
        verbose_name=_('merge automatically'),
        default=False,
        help_text=_(
            'Merge as soon as the change is approved, every required check passes, and the change window is open.'
        ),
    )
    # Two cached views of the branch, kept so the change request list can show them without
    # asking the database per row.
    #
    # This is the same split the plugin already makes for `status`: a cache for display and
    # filtering, never for a decision. The merge gate recomputes, and so does the change
    # request page. Nothing that permits a merge reads these.
    cached_conflicted = models.BooleanField(
        verbose_name=_('conflicts with main'),
        default=False,
        editable=False,
        help_text=_('Whether the branch genuinely conflicts with main, as of the last refresh.'),
    )
    cached_gates_cleared = models.BooleanField(
        verbose_name=_('gates cleared'),
        default=False,
        editable=False,
        help_text=_('Whether the policies and the required checks were satisfied at the last refresh.'),
    )
    policies = models.ManyToManyField(
        to='netbox_change_control.Policy',
        through='netbox_change_control.ChangeRequestPolicy',
        related_name='change_requests',
        blank=True,
        verbose_name=_('policies'),
    )

    class Meta:
        ordering = ('-created',)
        permissions = (
            ('override_window_changerequest', 'Can merge a change request outside its change window'),
            # Status is derived from the policy evaluation, so it is not an editable field.
            # The two transitions a person legitimately makes by hand get an action each,
            # rather than leaving the field writable and hoping nobody sets it to completed.
            ('abandon_changerequest', 'Can abandon a change request'),
            ('reopen_changerequest', 'Can reopen an abandoned change request'),
        )
        verbose_name = _('change request')
        verbose_name_plural = _('change requests')

    def __str__(self):
        return f'CR{self.pk}: {self.title}'

    def save(self, *args, **kwargs):
        # Keep the denormalised name in step while the branch exists, so it is still correct
        # after a rename and still present after a delete.
        if self.branch_id and self.branch:
            self.branch_name = self.branch.name
            if (update_fields := kwargs.get('update_fields')) is not None:
                kwargs['update_fields'] = {*update_fields, 'branch_name'}
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:changerequest', args=[self.pk])

    @property
    def branch_label(self):
        """
        The branch to show. Falls back to the stored name once the branch is deleted.
        """
        if self.branch_id and self.branch:
            return self.branch.name
        return self.branch_name or _('(deleted branch)')

    def clean(self):
        super().clean()
        if self.scheduled_start and self.scheduled_end:
            if self.scheduled_end <= self.scheduled_start:
                raise ValidationError(
                    {
                        'scheduled_end': _('The window must close after it opens.'),
                    }
                )

    @property
    def has_window(self):
        return bool(self.scheduled_start or self.scheduled_end)

    def window_state(self):
        """
        Return one of 'none', 'early', 'open' or 'closed'.

        A window with only one bound is half-open: a start alone means "not before", an end
        alone means "not after".
        """
        from django.utils import timezone

        if not self.has_window:
            return 'none'
        now = timezone.now()
        if self.scheduled_start and now < self.scheduled_start:
            return 'early'
        if self.scheduled_end and now > self.scheduled_end:
            return 'closed'
        return 'open'

    @property
    def window_is_open(self):
        return self.window_state() in ('none', 'open')

    @property
    def auto_merge_window_warning(self):
        """
        Report a change window too short for automatic merging to be relied on, else None.

        See netbox_change_control.automerge.unreliable_window for why a short window is a
        trap: the sweep which merges a request waiting on its window runs on a fixed
        interval, and can step over a window shorter than that interval.
        """
        from netbox_change_control.automerge import unreliable_window

        return unreliable_window(self)

    @property
    def branch_deleted(self):
        """
        True when the branch this request reviewed no longer exists.

        Such a request is a historical record only: it cannot be merged, its diff is gone,
        and its checks no longer have anything to inspect.
        """
        return self.branch_id is None

    def get_status_color(self):
        return ChangeRequestStatusChoices.colors.get(self.status)

    def get_priority_color(self):
        return ChangeRequestPriorityChoices.colors.get(self.priority)

    @property
    def is_approved(self):
        """
        The people gate: the policies are satisfied.

        This is not the same as being mergeable. Checks and the change window are separate
        gates, so an approved request can still be blocked. Use `is_ready_to_merge` to ask
        whether the change can actually go ahead.
        """
        return self.status == ChangeRequestStatusChoices.APPROVED

    @property
    def conflicts(self):
        """
        Return the branch diffs which genuinely conflict with main, evaluated now.

        Only conflicts where main has moved since the last sync are returned. Branching never
        advances a diff's baseline, so a field main touched before the sync stays flagged even
        though the branch already holds main's value. See netbox_change_control.conflicts.
        """
        from netbox_change_control.conflicts import conflicting_diffs

        return conflicting_diffs(None if self.branch_deleted else self.branch)

    @property
    def has_conflicts(self):
        """
        The cached answer, so a list of change requests costs no queries for this column.

        Reading it live means one query for the diffs and one against the branch for what main
        has done since the last sync, per row. Use `conflicts` where the actual objects are
        wanted, which is a single change request's own page.
        """
        return self.cached_conflicted

    @property
    def reconciled_conflicts(self):
        """
        Diffs branching still flags which a sync has already resolved. Shown as a note rather
        than a blocker, so a reviewer can see why branching's own page disagrees with ours.
        """
        from netbox_change_control.conflicts import stale_baseline_diffs

        return stale_baseline_diffs(None if self.branch_deleted else self.branch)

    @property
    def merge_indicator(self):
        """
        The authoritative answer to "can this merge right now", or None if there is no branch.

        Delegates to netbox-branching, so it accounts for every gate: our policies, checks
        and window, plus any validator another plugin has registered.
        """
        if self.branch_deleted:
            return None
        return self.branch.can_merge

    @property
    def is_ready_to_merge(self):
        """
        The authoritative answer, recomputed now. Used by the change request page and the API.
        """
        indicator = self.merge_indicator
        return bool(indicator and indicator.permitted)

    @property
    def cached_ready_to_merge(self):
        """
        The cheap answer, for a list of change requests. No queries at all.

        `cached_gates_cleared` covers the policies and the required checks, which only change
        when something happens and so can be cached on that event. The change window is the
        one gate that turns on the clock alone, with no event to hang a refresh on, so it is
        evaluated here from two fields already loaded on the row.

        It does not account for merge validators registered by other plugins, which
        `is_ready_to_merge` does. The change request page is the authoritative answer, and the
        merge gate itself never reads this.
        """
        return self.cached_gates_cleared and self.window_is_open

    @property
    def merge_blocked_reason(self):
        """
        Why the merge is blocked, or an empty string when it is not.
        """
        if self.branch_deleted:
            return str(_('The branch has been deleted.'))
        indicator = self.merge_indicator
        if indicator.permitted:
            return ''
        return indicator.message

    @property
    def is_open(self):
        return self.status in ChangeRequestStatusChoices.OPEN

    @property
    def can_be_submitted(self):
        """
        A draft is the only thing there is to submit.
        """
        return self.status == ChangeRequestStatusChoices.DRAFT

    @property
    def can_return_to_draft(self):
        """
        Anything open and already submitted can be pulled back.

        Including an approved one: an author who spots a problem after approval should be able
        to take the change off the table rather than race the merge, and pulling it back is
        the reversible way to do that. Abandoning is the one-way door.
        """
        return self.is_open and self.status != ChangeRequestStatusChoices.DRAFT

    def submit(self):
        """
        Put a draft into review, matching its policies. Returns True if the status moved.

        The status is set here rather than left to `refresh_status`, because that treats draft
        as the author's to hold and will not move a request out of it on its own.

        That also means the announcement has to be made here. `refresh_status` tells the
        reviewers only when it moves a request itself, and by the time it runs the status is
        already Needs review, so it sees no transition and says nothing. Submitting without
        telling anybody is the one outcome that makes the whole thing pointless.
        """
        if not self.can_be_submitted:
            return False

        from netbox_change_control.batching import batched, schedule_refresh
        from netbox_change_control.policy import sync_policies

        self.status = ChangeRequestStatusChoices.NEEDS_REVIEW
        self.save(update_fields=['status'])

        with batched():
            sync_policies(self)
            schedule_refresh(self)

        # The refresh may have carried it further, to Approved on a policy needing no human or
        # to Rejected on a standing rejection, and it announces those itself. Only the case it
        # cannot see is left here.
        self.refresh_from_db()
        if self.status == ChangeRequestStatusChoices.NEEDS_REVIEW:
            from netbox_change_control import events
            from netbox_change_control.notifications import notify_status_change

            notify_status_change(self, ChangeRequestStatusChoices.NEEDS_REVIEW)
            events.emit(self, events.CHANGE_REQUEST_REVIEW_REQUESTED)

        return True

    def return_to_draft(self):
        """
        Pull a submitted request back out of review. Returns True if the status moved.

        The reviews are kept. They may already be stale, and if they are not they still stand:
        a reviewer's position on the change does not stop being their position because the
        author wants to work on it some more. What stops is the merge, because a draft is not
        approved and the gate reads the status.
        """
        if not self.can_return_to_draft:
            return False

        self.status = ChangeRequestStatusChoices.DRAFT
        self.save(update_fields=['status'])

        from netbox_change_control.policy import refresh_cached_state

        refresh_cached_state(self)
        return True

    @property
    def can_be_abandoned(self):
        """
        An open request can be given up on. A finished one cannot: abandoning a merged change
        would claim it never happened.
        """
        return self.status in ChangeRequestStatusChoices.OPEN

    @property
    def can_be_reopened(self):
        """
        Only an abandoned request comes back.

        Completed is the record of a merge that actually happened, so reopening it would
        invite a second merge of a branch that is already in main.
        """
        return self.status == ChangeRequestStatusChoices.ABANDONED

    def abandon(self):
        """
        Give up on this change request. Returns True if the status moved.
        """
        if not self.can_be_abandoned:
            return False
        self.status = ChangeRequestStatusChoices.ABANDONED
        self.save(update_fields=['status'])
        return True

    def reopen(self):
        """
        Bring an abandoned request back, then let the evaluation decide where it lands.

        It returns to Draft rather than to whatever it held before, because the reviews it
        carried may since have gone stale and the policies may since have changed. Refreshing
        computes the honest answer instead of restoring a remembered one.
        """
        if not self.can_be_reopened:
            return False

        from netbox_change_control.policy import refresh_status, sync_policies

        self.status = ChangeRequestStatusChoices.DRAFT
        self.save(update_fields=['status'])
        if not self.branch_deleted:
            sync_policies(self)
        refresh_status(self)
        return True

    def evaluate(self):
        """
        Return a PolicyEvaluation describing which rules are satisfied.
        """
        from netbox_change_control.policy import evaluate_change_request

        return evaluate_change_request(self)


class ChangeRequestPolicy(models.Model):
    """
    Through table binding a policy to a change request.

    `matched_object_types` records which object types in the branch caused the policy to
    attach, which is what the change request page shows beside each policy.

    There is no "attached by hand" state. Which policies govern a change is decided from the
    objects its branch touches, and nothing else can attach one, which is the whole point:
    an author who could choose would choose the weakest.
    """

    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='policy_bindings',
    )
    policy = models.ForeignKey(
        to='netbox_change_control.Policy',
        on_delete=models.PROTECT,
        related_name='policy_bindings',
    )
    matched_object_types = models.JSONField(
        verbose_name=_('matched object types'),
        default=list,
        blank=True,
        help_text=_('The object types in the branch which caused this policy to attach.'),
    )

    class Meta:
        ordering = ('policy__weight', 'policy__name')
        constraints = (
            models.UniqueConstraint(
                fields=('change_request', 'policy'),
                name='%(app_label)s_%(class)s_unique_request_policy',
            ),
        )
        # Django creates four permissions for every model. No view of this plugin reads any of
        # these, and granting one was a trap: an administrator reaching for
        # delete_changerequestpolicy to detach a policy by hand gets it back at the next
        # re-match. Restore them if this table ever grows a view.
        default_permissions = ()
        verbose_name = _('change request policy')
        verbose_name_plural = _('change request policies')

    def __str__(self):
        return f'{self.change_request} / {self.policy}'


class Review(NetBoxModel):
    """
    One reviewer's decision on a change request. A reviewer holds at most one review per
    change request; submitting again replaces the previous decision.
    """

    change_request = models.ForeignKey(
        to=ChangeRequest,
        on_delete=models.CASCADE,
        related_name='reviews',
        verbose_name=_('change request'),
    )
    reviewer = models.ForeignKey(
        to='users.User',
        on_delete=models.PROTECT,
        related_name='change_control_reviews',
        verbose_name=_('reviewer'),
    )
    decision = models.CharField(
        verbose_name=_('decision'),
        max_length=32,
        choices=ReviewDecisionChoices,
    )
    comment = models.TextField(
        verbose_name=_('comment'),
        blank=True,
    )
    branch_change_time = models.DateTimeField(
        verbose_name=_('branch state at review'),
        null=True,
        blank=True,
        editable=False,
        help_text=_('Timestamp of the most recent branch change when this review was submitted.'),
    )

    class Meta:
        ordering = ('-created',)
        constraints = (
            models.UniqueConstraint(
                fields=('change_request', 'reviewer'),
                name='%(app_label)s_%(class)s_unique_request_reviewer',
            ),
        )
        verbose_name = _('review')
        verbose_name_plural = _('reviews')

    def __str__(self):
        return f'{self.reviewer} {self.get_decision_display()} {self.change_request}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:changerequest', args=[self.change_request_id])

    def get_decision_color(self):
        return ReviewDecisionChoices.colors.get(self.decision)

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_decision = instance.decision
        return instance

    def save(self, *args, refresh_snapshot=None, **kwargs):
        """
        Record which branch state this review was made against.

        The snapshot must refresh only when the reviewer actually restates their position:
        on creation, when the decision changes, or when a caller says so explicitly by
        passing `refresh_snapshot=True` (what the review form does).

        It must NOT refresh on an incidental save. Correcting a typo in the comment, a bulk
        edit, or an API PATCH of an unrelated field would otherwise silently revalidate a
        stale approval against branch content the reviewer never saw.
        """
        from netbox_change_control.policy import branch_last_change_time

        if refresh_snapshot is None:
            refresh_snapshot = self._state.adding or getattr(self, '_loaded_decision', self.decision) != self.decision

        if refresh_snapshot and self.change_request_id:
            self.branch_change_time = branch_last_change_time(self.change_request.branch)
            # branch_last_change_time returns None for a deleted branch, which leaves the
            # review permanently current. That is correct: it can never go stale again.
            if 'update_fields' in kwargs and kwargs['update_fields'] is not None:
                kwargs['update_fields'] = {*kwargs['update_fields'], 'branch_change_time'}

        super().save(*args, **kwargs)
        self._loaded_decision = self.decision

    @property
    def is_stale(self):
        """
        True when the branch has changed since this review was submitted.

        A stale approval must not count toward a policy rule, otherwise a reviewer's sign-off
        silently covers work they never saw.
        """
        from netbox_change_control.policy import branch_last_change_time

        if self.branch_change_time is None:
            return False
        latest = branch_last_change_time(self.change_request.branch)
        if latest is None:
            return False
        return latest > self.branch_change_time

    def clean(self):
        super().clean()
        if self.decision == ReviewDecisionChoices.REJECT and not self.comment:
            raise ValidationError({'comment': _('A comment is required when requesting changes.')})
        if self.change_request_id and self.reviewer_id:
            if self.change_request.requester_id == self.reviewer_id:
                raise ValidationError({'reviewer': _('A user cannot review their own change request.')})
