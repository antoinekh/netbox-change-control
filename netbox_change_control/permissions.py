"""
Every permission the plugin checks, and the helpers that check them.

Views and the REST API ask here rather than spelling a permission name or a restricted lookup
themselves: when each did its own, the interface and the API drifted apart, one honouring an
object constraint or an ownership rule that the other ignored.
"""

from contextvars import ContextVar

from django.db import transaction
from django.shortcuts import get_object_or_404
from netbox.context import current_request

__all__ = (
    'ABANDON_PERMISSION',
    'ADD_COMMENT_PERMISSION',
    'ADD_REVIEW_PERMISSION',
    'BYPASS_PERMISSION',
    'CHANGE_CHECK_PERMISSION',
    'CHANGE_COMMENT_PERMISSION',
    'CHANGE_PERMISSION',
    'MERGE_PERMISSION',
    'OVERRIDE_WINDOW_PERMISSION',
    'REOPEN_PERMISSION',
    'acting_user_has_perm',
    'current_user_has_perm',
    'may_rewrite',
    'own_statements',
    'restricted_or_404',
    'running_job',
    'save_if_permitted',
)

# NetBox parses a permission name as <app_label>.<action>_<model> using rsplit('_', 1), so
# the trailing component must be a real model of this plugin. A name that does not resolve
# cannot be granted by any object permission, leaving the exemption available to superusers
# only, which is silently broken rather than merely inconvenient.
BYPASS_PERMISSION = 'netbox_change_control.bypass_policy'
OVERRIDE_WINDOW_PERMISSION = 'netbox_change_control.override_window_changerequest'

# Status is derived from the policy evaluation, so it is not an editable field. These two are
# the transitions a person makes by hand, and each is granted separately: giving up on a change
# and taking one back up are different decisions from editing its title.
# Submitting and withdrawing are two halves of one control the author holds over whether the
# change is under review, so they share the ordinary change permission rather than inventing
# two more. Abandoning is different: it is a one-way door, and it has its own.
CHANGE_PERMISSION = 'netbox_change_control.change_changerequest'
ABANDON_PERMISSION = 'netbox_change_control.abandon_changerequest'
REOPEN_PERMISSION = 'netbox_change_control.reopen_changerequest'

ADD_REVIEW_PERMISSION = 'netbox_change_control.add_review'
ADD_COMMENT_PERMISSION = 'netbox_change_control.add_changecomment'
# Resolving and reopening a thread, whoever wrote it; rewriting one is the author's alone.
CHANGE_COMMENT_PERMISSION = 'netbox_change_control.change_changecomment'
# Re-running the checks and reporting a result both write check rows.
CHANGE_CHECK_PERMISSION = 'netbox_change_control.change_mergecheck'

# netbox-branching's own action. An automatic merge runs as the requester, so it needs this too.
MERGE_PERMISSION = 'netbox_branching.merge_branch'


# The background job running in this context, set and cleared by NetBox's job signals.
running_job = ContextVar('netbox_change_control.running_job', default=None)


def current_user_has_perm(permission):
    """
    Return whether the current request's user holds `permission`.

    True when there is no request: a write from a migration, a management command or a
    background job is not an interactive edit. A custom script runs inside a request with the
    user who started it, so it is checked like any other edit.
    """
    request = current_request.get()
    if request is None:
        return True
    return _holds(getattr(request, 'user', None), permission)


def acting_user_has_perm(permission):
    """
    Return whether the user acting now holds `permission`: the user of the current request, or
    else the user who started the running background job. False when there is neither.

    A merge runs in netbox-branching's background job, with no request, so the job's user is
    the one who merges.
    """
    request = current_request.get()
    if request is not None:
        return _holds(getattr(request, 'user', None), permission)
    job = running_job.get()
    return job is not None and _holds(job.user, permission)


def _holds(user, permission):
    return user is not None and user.is_authenticated and user.has_perm(permission)


def restricted_or_404(queryset, user, action, **lookup):
    """
    The object `lookup` names, among those `user` may `action`, constraints included; a 404
    otherwise, which is how NetBox answers an object-level miss.
    """
    return get_object_or_404(queryset.restrict(user, action), **lookup)


def may_rewrite(user, author_id):
    """
    Whether `user` may rewrite a statement made by `author_id`: their own, or any for a superuser.

    A review or a comment is a statement attributed to a person, so rewriting somebody else's
    puts words in their mouth in the record the merge decision rests on. Every place that edits
    one asks here, or narrows through `own_statements`, so the interface and the REST API cannot
    disagree.
    """
    return user.is_superuser or author_id == user.pk


def own_statements(queryset, user, author_field):
    """
    Narrow `queryset` to the statements `user` may rewrite. See `may_rewrite`.
    """
    if user.is_superuser:
        return queryset
    return queryset.filter(**{author_field: user})


class _Refused(Exception):
    pass


def save_if_permitted(user, permission, obj, save):
    """
    Run `save()`, and keep what it wrote only if `user` holds `permission` on the saved `obj`.

    A constraint on an add permission describes the object being created, so it can only be
    tested once the object exists. NetBox's own edit views do the same: write, test, and roll
    back on a refusal, which also discards any event the write queued for the commit.
    """
    try:
        with transaction.atomic():
            save()
            if not user.has_perm(permission, obj):
                raise _Refused
    except _Refused:
        return False
    return True
