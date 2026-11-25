"""
Example custom checks, so the policy form has something to offer beyond the built-ins.

Both are registered with the policy scope, which is what puts them in the **Registered
checks** list on a policy. A check with the default scope applies everywhere and is
deliberately absent from that list, because selecting it would change nothing.

These live in the development plugin. They are examples of the extension point described in
docs/custom-checks.md, not features of netbox-change-control.
"""

from netbox_change_control.checks import CheckResult

__all__ = ('change_is_small', 'description_is_written')

#: Objects a branch may touch before the check complains.
LARGE_CHANGE = 10


def change_is_small(change_request):
    """
    Refuse a branch touching more than LARGE_CHANGE objects.

    A sprawling branch is hard to review honestly: past a certain size a reviewer skims. The
    remedy is to split it, not to read faster.
    """
    from netbox_branching.models import ChangeDiff

    if change_request.branch_deleted:
        return CheckResult.skipped('The branch no longer exists.')

    count = ChangeDiff.objects.filter(branch=change_request.branch).count()
    if count > LARGE_CHANGE:
        return CheckResult.failed(f'{count} objects changed, more than the {LARGE_CHANGE} this policy allows.')
    return CheckResult.passed(f'{count} object(s) changed.')


def description_is_written(change_request):
    """
    Refuse a change request whose description is empty or perfunctory.

    Reads the change request itself rather than the branch, which is the pattern for anything
    a policy's object-type scope cannot express.
    """
    description = (change_request.description or '').strip()
    if not description:
        return CheckResult.failed('No description. Say what is changing and why.')
    if len(description) < 20:
        return CheckResult.failed(f'The description is {len(description)} characters. Say a little more.')
    return CheckResult.passed(f'{len(description)} characters.')
