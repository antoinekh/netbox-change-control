"""
Permission lookups that read the current request.

Both callers need the same thing, and they differ only in what an absent request means, so
that decision is the caller's rather than duplicated in two near-identical helpers.
"""

from netbox.context import current_request

__all__ = (
    'ABANDON_PERMISSION',
    'BYPASS_PERMISSION',
    'OVERRIDE_WINDOW_PERMISSION',
    'REOPEN_PERMISSION',
    'current_user_has_perm',
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
ABANDON_PERMISSION = 'netbox_change_control.abandon_changerequest'
REOPEN_PERMISSION = 'netbox_change_control.reopen_changerequest'


def current_user_has_perm(permission, *, without_request):
    """
    Return whether the current request's user holds `permission`.

    `without_request` is the answer when there is no request at all, which the two callers
    disagree about:

    - `protect_main` passes True. A write with no request comes from a migration, a script or
      a background job, which are not interactive edits.
    - A change window passes False. A script merging at the wrong hour is exactly what a
      window exists to stop.
    """
    request = current_request.get()
    if request is None:
        return without_request

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return False

    return user.has_perm(permission)
