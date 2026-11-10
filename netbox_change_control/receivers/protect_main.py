"""
`protect_main`: refuse a write to main unless it happens inside a branch.

When enabled, writes to branching-supported models are refused unless they happen inside a
branch. Users holding `netbox_change_control.bypass_policy` are exempt.
"""

from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver
from netbox.plugins import get_plugin_config
from netbox_branching.contextvars import active_branch
from utilities.exceptions import AbortRequest

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

__all__ = (
    'protect_main_on_delete',
    'protect_main_on_save',
)


# Raised as AbortRequest, not PermissionDenied. NetBox catches AbortRequest from a signal
# receiver and shows its message where the user is working: as a form error on the edit page,
# a toast on a delete, and `detail` on a 400 from the REST API. PermissionDenied instead
# produced a bare "Access Denied" page with this text discarded, which told the user nothing
# about what to do and read like a broken permission rather than a deliberate policy.
MESSAGE = 'Direct changes to main are disabled. Create a branch, make the change there, and open a change request.'


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
