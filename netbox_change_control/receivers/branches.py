"""
Follow the branch: its merge completes the request, its name is kept, and a sync re-evaluates.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert, post_sync

from netbox_change_control.batching import batched, schedule_refresh
from netbox_change_control.lifecycle import complete
from netbox_change_control.models import ChangeRequest
from netbox_change_control.policy import sync_policies

__all__ = (
    'complete_on_merge',
    'refresh_on_branch_change',
    'track_branch_name',
)


#
# Branch lifecycle.
#


@receiver(post_merge)
def complete_on_merge(sender, branch, **kwargs):
    """
    Close the change request once its branch has merged.
    """
    if change_request := ChangeRequest.objects.filter(branch=branch).first():
        complete(change_request)


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

    # sync_policies can attach or detach several policies, each its own signal.
    with batched():
        sync_policies(change_request)
        schedule_refresh(change_request)
