"""
Keep `status` and the attached policies in step with what can change them.

`status` is a cached view of the policy evaluation, so every write that can move the answer
recomputes it: a review, a policy binding, a policy or rule edit, a membership change, and an
edit inside the branch. A branch growing a new object type re-matches the policies too.
"""

from core.models import ObjectChange
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from netbox_branching.contextvars import active_branch
from netbox_branching.models import ChangeDiff
from users.models import Group, User

from netbox_change_control.batching import batched, schedule_refresh
from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.lifecycle import refresh_status
from netbox_change_control.models import (
    ChangeRequest,
    ChangeRequestPolicy,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.policy import sync_policies
from netbox_change_control.receivers.deleting import is_being_deleted

__all__ = (
    'emit_on_review_submitted',
    'invalidate_approval_on_branch_change',
    'refresh_on_group_membership_change',
    'refresh_on_policy_binding_change',
    'refresh_on_policy_change',
    'refresh_on_review_change',
    'refresh_on_rule_membership_change',
    'resync_policies_on_new_diff',
)


#
# Keep ChangeRequest.status consistent with its policy evaluation.
#
# `status` is a cached view of the evaluation. Recomputing it only where reviews are
# submitted through the UI leaves it stale whenever a review arrives by another path: the
# REST API, the object edit form, a bulk edit, a management command or the ORM.
#


@receiver([post_save, post_delete], sender=Review)
def refresh_on_review_change(sender, instance, **kwargs):
    _refresh(instance.change_request_id)


@receiver(post_save, sender=Review)
def emit_on_review_submitted(sender, instance, created, **kwargs):
    """
    A reviewer stating a position is worth announcing even when it does not move the status.

    Four approvals against a rule needing five change nothing about the request, but each one
    is a fact an integration may want. The status transitions are emitted separately by
    refresh_status.
    """
    if is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return

    from netbox_change_control import events

    events.emit(change_request, events.REVIEW_SUBMITTED)


@receiver([post_save, post_delete], sender=ChangeRequestPolicy)
def refresh_on_policy_binding_change(sender, instance, **kwargs):
    """
    Attaching or detaching a policy changes which checks apply, not just who must approve.

    Every built-in check is policy-scoped, and a change request is created before its
    policies are matched, so the run at creation sees no policies and therefore no checks.
    Without running them here the panel sits at "pending" until somebody presses Re-run.
    """
    if is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return

    # Scheduled rather than run here. Attaching a policy is one signal per policy, so a
    # request governed by three of them refreshed three times and ran every check three times
    # for the same answer. Inside a batched() block this collapses to one.
    schedule_refresh(change_request)


def _refresh(change_request_id):
    """
    Reload the change request so the evaluation reads committed related rows, then refresh.
    """
    from netbox_change_control.models import ChangeRequest

    if is_being_deleted(change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=change_request_id).first()
    if change_request is not None:
        refresh_status(change_request)


#
# Policy reevaluation.
#
# Changing a rule, its reviewers, or a user's group membership can make a previously
# approved request non-compliant. Every open request bound to the affected policy is
# re-evaluated.
#


def _refresh_for_policies(policy_ids):
    requests = ChangeRequest.objects.filter(
        policy_bindings__policy_id__in=policy_ids,
        status__in=ChangeRequestStatusChoices.OPEN,
    ).distinct()
    # One rule edit can reach the same request through more than one policy, so the batch
    # deduplicates as well as collapsing.
    with batched():
        for change_request in requests:
            schedule_refresh(change_request)


@receiver([post_save, post_delete], sender=Policy)
@receiver([post_save, post_delete], sender=PolicyRule)
def refresh_on_policy_change(sender, instance, **kwargs):
    policy_id = instance.pk if sender is Policy else instance.policy_id
    _refresh_for_policies([policy_id])


@receiver(m2m_changed, sender=PolicyRule.groups.through)
@receiver(m2m_changed, sender=PolicyRule.users.through)
def refresh_on_rule_membership_change(sender, instance, action, **kwargs):
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    _refresh_for_policies([instance.policy_id])


def refresh_on_group_membership_change(sender, instance, action, **kwargs):
    """
    A user joining or leaving a group changes who is eligible for a rule.

    Connected explicitly below rather than with a bare @receiver, so it does not run on
    every many-to-many change in NetBox.
    """
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    policy_ids = list(PolicyRule.objects.values_list('policy_id', flat=True).distinct())
    _refresh_for_policies(policy_ids)


m2m_changed.connect(refresh_on_group_membership_change, sender=User.groups.through)
m2m_changed.connect(refresh_on_group_membership_change, sender=Group.users.through)


@receiver(post_save, sender=ObjectChange)
def invalidate_approval_on_branch_change(sender, instance, **kwargs):
    """
    Recompute the status when the branch content moves.

    Staleness is derived from the branch's newest change, so editing an object inside a
    branch can invalidate an existing approval. No other signal fires for that: the edit
    touches a device or a circuit, not a review or a policy. Without this hook a change
    request stays "Approved" while its approvals have gone stale.

    The guard keeps this cheap. Only an approved or rejected request can be invalidated by
    new content; a request already awaiting review has nothing to recompute, so the common
    case costs one indexed query.
    """
    branch = active_branch.get()
    if branch is None:
        return

    change_request = ChangeRequest.objects.filter(
        branch=branch,
        status__in=(ChangeRequestStatusChoices.APPROVED, ChangeRequestStatusChoices.REJECTED),
    ).first()
    if change_request is None:
        return

    refresh_status(change_request)


def _scope_may_have_changed(diff):
    """
    Cheap test for whether one new ChangeDiff can change which policies match.

    A policy scoped by object type cannot change its answer for the second object of a type
    the branch already held, so the common case of a bulk edit costs one indexed query per
    object rather than a full re-match.

    A policy carrying conditions reads the changed objects themselves, so for those any new
    object can change the answer and the re-match has to run.
    """
    first_of_its_type = (
        not ChangeDiff.objects.filter(branch_id=diff.branch_id, object_type_id=diff.object_type_id)
        .exclude(pk=diff.pk)
        .exists()
    )
    if first_of_its_type:
        return True
    return Policy.objects.filter(enabled=True).exclude(conditions__isnull=True).exists()


@receiver(post_save, sender=ChangeDiff)
def resync_policies_on_new_diff(sender, instance, created, **kwargs):
    """
    Re-match the policies when the branch starts touching something new.

    Which policies govern a change request is decided from the object types in its branch.
    That question used to be asked twice only: when the author pressed Submit for review, and
    when branching synced or reverted the branch. An ordinary edit inside a branch writes an
    ObjectChange and a ChangeDiff, and neither re-asked it, so the governing set stayed frozen
    against the branch as it looked at submission.

    That was a way round the gate. An author could open a request on a branch touching only
    low-risk objects, collect the light approval that attracted, then add the real change to
    the same branch. The approvals went stale and the status returned to Needs review, but the
    policy governing the new object type never attached, so the same reviewer could approve a
    second time and merge work nobody with the authority to judge it had seen.

    A ChangeDiff is created once per changed object, which makes it the cheapest signal
    meaning "this branch now holds something it did not before". Reacting to ObjectChange
    instead would fire on every save of every object for the same answer.
    """
    if not created:
        return

    change_request = ChangeRequest.objects.filter(
        branch_id=instance.branch_id,
        status__in=ChangeRequestStatusChoices.OPEN,
    ).first()
    if change_request is None or is_being_deleted(change_request.pk):
        return

    if not _scope_may_have_changed(instance):
        return

    # A newly attached policy brings rules with it, so the request may no longer be satisfied.
    # sync_policies writes bindings, whose own receiver schedules the refresh; scheduling it
    # here too covers the case where the matched set turned out to be unchanged.
    with batched():
        sync_policies(change_request)
        schedule_refresh(change_request)
