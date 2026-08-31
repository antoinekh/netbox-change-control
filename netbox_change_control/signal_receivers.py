"""
Signal receivers implementing `protect_main`.

When enabled, writes to branching-supported models are refused unless they happen inside a
branch. Users holding `netbox_change_control.bypass_policy` are exempt.
"""

from contextvars import ContextVar

from core.models import ObjectChange
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from netbox.plugins import get_plugin_config
from netbox_branching.contextvars import active_branch
from netbox_branching.models import Branch, ChangeDiff
from netbox_branching.signals import post_merge, post_revert, post_sync
from users.models import Group, User
from utilities.exceptions import AbortRequest

from netbox_change_control.automerge import try_auto_merge
from netbox_change_control.checks import run_checks
from netbox_change_control.choices import ChangeRequestStatusChoices, MergeCheckStatusChoices
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    ChangeRequestPolicy,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.permissions import BYPASS_PERMISSION, current_user_has_perm
from netbox_change_control.policy import refresh_status, sync_policies

__all__ = (
    'auto_merge_on_check_result',
    'complete_on_merge',
    'emit_on_review_submitted',
    'invalidate_approval_on_branch_change',
    'mark_change_request_deleting',
    'protect_main_on_delete',
    'protect_main_on_save',
    'refresh_on_branch_change',
    'refresh_on_group_membership_change',
    'refresh_on_policy_binding_change',
    'refresh_on_policy_change',
    'refresh_on_review_change',
    'refresh_on_rule_membership_change',
    'rerun_checks_on_comment_change',
    'rerun_checks_on_diff_change',
    'run_checks_for_new_request',
    'track_branch_name',
    'unmark_change_request_deleting',
)

# Raised as AbortRequest, not PermissionDenied. NetBox catches AbortRequest from a signal
# receiver and shows its message where the user is working: as a form error on the edit page,
# a toast on a delete, and `detail` on a 400 from the REST API. PermissionDenied instead
# produced a bare "Access Denied" page with this text discarded, which told the user nothing
# about what to do and read like a broken permission rather than a deliberate policy.
MESSAGE = 'Direct changes to main are disabled. Create a branch, make the change there, and open a change request.'

# Change requests whose deletion is under way.
#
# Deleting a change request cascades to its reviews, comments and checks, and the receivers
# below react to each of those by refreshing the request. Refreshing re-creates the check
# rows, which are then inserted against a change request row that is about to disappear, and
# the whole delete fails on commit with a foreign key violation. Deleting a change request
# that carried a comment thread was a server error because of it.
#
# Django's collector sends every pre_delete before any post_delete, so marking the request
# here is enough to silence the receivers for the rest of the cascade.
_deleting = ContextVar('netbox_change_control.deleting_change_requests', default=frozenset())


def _is_being_deleted(change_request_id):
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


def _in_protected_scope(instance):
    """
    Return True when `protect_main_scope` covers this model.

    An empty scope protects every branching-supported model. A non-empty scope protects only
    the listed models, so you can require branches for circuits while leaving the rest of
    NetBox editable on main.

    Entries are `app_label.modelname` or `app_label.*`, matched case-insensitively.
    """
    scope = get_plugin_config('netbox_change_control', 'protect_main_scope') or []
    if not scope:
        return True

    meta = instance._meta
    label = f'{meta.app_label}.{meta.model_name}'.lower()
    wildcard = f'{meta.app_label}.*'.lower()
    entries = {str(entry).lower() for entry in scope}
    return label in entries or wildcard in entries


def _is_protected(instance):
    """
    Return True when this write must be refused.
    """
    if not get_plugin_config('netbox_change_control', 'protect_main'):
        return False

    from netbox_branching.utilities import supports_branching

    # A write inside a branch is exactly what we want people to do.
    if active_branch.get() is not None:
        return False

    # Only models which branching can track are protected. Our own models, jobs, sessions
    # and similar infrastructure must keep working on main.
    if not supports_branching(type(instance)):
        return False

    # Change-control records themselves must stay writable on main, otherwise protect_main
    # would stop anyone from opening a change request.
    if isinstance(
        instance, (ChangeComment, ChangeRequest, ChangeRequestPolicy, MergeCheck, Policy, PolicyRule, Review)
    ):
        return False

    return _in_protected_scope(instance)


@receiver(pre_save)
def protect_main_on_save(sender, instance, **kwargs):
    if _is_protected(instance) and not current_user_has_perm(BYPASS_PERMISSION, without_request=True):
        raise AbortRequest(MESSAGE)


@receiver(pre_delete)
def protect_main_on_delete(sender, instance, **kwargs):
    if _is_protected(instance) and not current_user_has_perm(BYPASS_PERMISSION, without_request=True):
        raise AbortRequest(MESSAGE)


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
    if _is_being_deleted(instance.change_request_id):
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
    if _is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return

    # Status first: run_checks may auto-merge, and it should decide against a current status.
    refresh_status(change_request)
    run_checks(change_request)


def _refresh(change_request_id):
    """
    Reload the change request so the evaluation reads committed related rows, then refresh.
    """
    from netbox_change_control.models import ChangeRequest

    if _is_being_deleted(change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=change_request_id).first()
    if change_request is not None:
        refresh_status(change_request)


#
# Branch lifecycle.
#


@receiver(post_merge)
def complete_on_merge(sender, branch, **kwargs):
    """
    Close the change request once its branch has merged.
    """
    change_request = ChangeRequest.objects.filter(branch=branch).first()
    if change_request is None:
        return
    if change_request.status == ChangeRequestStatusChoices.COMPLETED:
        return
    change_request.status = ChangeRequestStatusChoices.COMPLETED
    change_request.save(update_fields=['status'])

    # refresh_status emits the other transitions, but completion is set here and never passes
    # through it, so this is the only place the event can come from.
    from netbox_change_control import events

    events.emit(change_request, events.CHANGE_REQUEST_COMPLETED)


@receiver(post_save, sender=Branch)
def track_branch_name(sender, instance, **kwargs):
    """
    Keep the denormalised branch name in step with the branch.

    The name is stored on the change request so the record stays readable once the branch is
    deleted, and it was written only when the change request itself was saved. A rename left
    it behind: the page read the live branch and showed the new name, while the branch filter
    and the global `q` search read the stored copy and still matched the old one. Searching for
    a branch by the name on screen found nothing.
    """
    ChangeRequest.objects.filter(branch=instance).exclude(branch_name=instance.name).update(branch_name=instance.name)


@receiver([post_sync, post_revert])
def refresh_on_branch_change(sender, branch, **kwargs):
    """
    Re-evaluate after the branch content moves.

    Syncing pulls new commits in, which makes existing reviews stale. Because stale
    approvals no longer count, an approved request drops back to Needs review on its own.
    The policy scope is recomputed too, since the branch may now touch new object types.
    """
    change_request = ChangeRequest.objects.filter(branch=branch).first()
    if change_request is None:
        return
    sync_policies(change_request)
    run_checks(change_request)
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
    for change_request in requests:
        refresh_status(change_request)


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


@receiver([post_save, post_delete], sender=ChangeComment)
def rerun_checks_on_comment_change(sender, instance, **kwargs):
    """
    Re-run the checks when a comment thread is opened, resolved or removed.

    The `threads-resolved` built-in reads the comment threads, so without this its result
    would go stale the moment somebody resolved a thread.
    """
    if _is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return
    run_checks(change_request)


@receiver(post_save, sender=MergeCheck)
def auto_merge_on_check_result(sender, instance, **kwargs):
    """
    A passing check can be the last gate a request was waiting on.
    """
    if not instance.is_passing or _is_being_deleted(instance.change_request_id):
        return
    change_request = ChangeRequest.objects.filter(pk=instance.change_request_id).first()
    if change_request is None:
        return
    try_auto_merge(change_request)


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
    if change_request is None or _is_being_deleted(change_request.pk):
        return

    if not _scope_may_have_changed(instance):
        return

    sync_policies(change_request)
    # A newly attached policy brings rules with it, so the request may no longer be satisfied.
    # sync_policies writes bindings, whose own receiver refreshes the status, but it does
    # nothing when the matched set is unchanged. Refreshing here covers both paths.
    refresh_status(change_request)


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
    if change_request is None or _is_being_deleted(change_request.pk):
        return

    stored = change_request.checks.filter(name='no-conflicts').first()
    if stored is None:
        return

    # Use the same real-versus-reconciled test the check applies, or this would keep
    # re-running checks over a flag the check deliberately ignores.
    from netbox_change_control.conflicts import conflicting_diffs

    conflicted = bool(conflicting_diffs(change_request.branch))
    if conflicted == (stored.status != MergeCheckStatusChoices.SUCCESS):
        # The stored result already agrees with reality.
        return

    run_checks(change_request)
