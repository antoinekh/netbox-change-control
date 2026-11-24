"""
Follow the branch: its merge completes the request, a failed merge is recorded, its revert
returns it to Draft, its deletion abandons it, its name is kept, and a sync re-evaluates.
"""

from core.choices import JobStatusChoices
from core.signals import job_end
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from netbox_branching.jobs import MergeBranchJob
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert, post_sync

from netbox_change_control import lifecycle
from netbox_change_control.batching import batched, schedule_refresh
from netbox_change_control.models import ChangeRequest
from netbox_change_control.policy import sync_policies

__all__ = (
    'abandon_on_branch_delete',
    'complete_on_merge',
    'record_failed_merge',
    'refresh_on_branch_change',
    'return_to_draft_on_revert',
    'track_branch_name',
)


#
# Branch lifecycle.
#


@receiver(post_merge)
def complete_on_merge(sender, branch, user=None, **kwargs):
    """
    Close the change request once its branch has merged.
    """
    if change_request := ChangeRequest.objects.filter(branch=branch).first():
        lifecycle.complete(change_request, user)


@receiver(job_end)
def record_failed_merge(sender, **kwargs):
    """
    Record a merge job that failed on the change request of its branch.

    netbox-branching puts the branch back to ready and sends no signal, so the job is the only
    trace of the failure.
    """
    if sender.name != MergeBranchJob.name:
        return
    if sender.status not in (JobStatusChoices.STATUS_ERRORED, JobStatusChoices.STATUS_FAILED):
        return
    if sender.object_type_id != ContentType.objects.get_for_model(Branch).pk:
        return
    if change_request := ChangeRequest.objects.filter(branch_id=sender.object_id).first():
        lifecycle.record_failed_merge(change_request, sender.user)


@receiver(post_revert)
def return_to_draft_on_revert(sender, branch, user=None, **kwargs):
    """
    Return a completed change request to Draft once its branch has been reverted.
    """
    change_request = ChangeRequest.objects.filter(branch=branch).first()
    if change_request is None:
        return

    if not lifecycle.revert(change_request, user):
        _rematch(change_request)


@receiver(pre_delete, sender=Branch)
def abandon_on_branch_delete(sender, instance, **kwargs):
    """
    Abandon the open change request of a branch being deleted.

    An open request without a branch can never merge, so it would wait for reviews that decide
    nothing. Run before the delete, while the request still points at the branch, and inside
    the delete's transaction, so a delete that fails leaves the request open.
    """
    if change_request := ChangeRequest.objects.filter(branch=instance).first():
        lifecycle.abandon(change_request)


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


@receiver(post_sync)
def refresh_on_branch_change(sender, branch, **kwargs):
    """
    Re-evaluate after the branch content moves.

    Syncing pulls new commits in, which makes existing reviews stale. Because stale
    approvals no longer count, an approved request drops back to Needs review on its own.
    The policy scope is recomputed too, since the branch may now touch new object types.
    """
    if change_request := ChangeRequest.objects.filter(branch=branch).first():
        _rematch(change_request)


def _rematch(change_request):
    # sync_policies can attach or detach several policies, each its own signal.
    with batched():
        sync_policies(change_request)
        schedule_refresh(change_request)
