"""
Branch action validators registered with netbox-branching.

Branching calls these from `Branch.can_merge`, so a refusal both blocks the merge and
hides the merge button in the UI.
"""

from netbox.plugins import get_plugin_config
from netbox_branching.utilities import BranchActionIndicator

from netbox_change_control.permissions import OVERRIDE_WINDOW_PERMISSION, current_user_has_perm

__all__ = (
    'blocking_checks',
    'require_approved_change_request',
)


def require_approved_change_request(branch):
    """
    Permit a merge only when the branch carries an approved change request whose policies
    are still satisfied.
    """
    if not get_plugin_config('netbox_change_control', 'enforce_merge_gate'):
        return BranchActionIndicator(True)

    from netbox_change_control.choices import ChangeRequestStatusChoices
    from netbox_change_control.models import ChangeRequest

    change_request = ChangeRequest.objects.filter(branch=branch).first()
    if change_request is None:
        return BranchActionIndicator(
            False,
            'This branch has no change request. Open one and obtain approval before merging.',
        )

    if change_request.status == ChangeRequestStatusChoices.COMPLETED:
        return BranchActionIndicator(False, f'{change_request} is already completed.')

    if not change_request.is_approved:
        return BranchActionIndicator(
            False,
            f'{change_request} is not approved (status: {change_request.get_status_display()}).',
        )

    # Re-evaluate rather than trusting the stored status. Policies or group membership may
    # have changed since approval was recorded.
    evaluation = change_request.evaluate()
    if not evaluation.satisfied:
        return BranchActionIndicator(
            False,
            f'{change_request} no longer satisfies its policies: ' + ' '.join(evaluation.reasons()),
        )

    # A change window is a third, independent gate. Holders of override_window bypass it,
    # which is what an incident response needs.
    if not change_request.window_is_open and not current_user_has_perm(
        OVERRIDE_WINDOW_PERMISSION, without_request=False
    ):
        state = change_request.window_state()
        if state == 'early':
            return BranchActionIndicator(
                False,
                f'The change window opens at {change_request.scheduled_start:%Y-%m-%d %H:%M %Z}.',
            )
        return BranchActionIndicator(
            False,
            f'The change window closed at {change_request.scheduled_end:%Y-%m-%d %H:%M %Z}.',
        )

    # Required checks gate the merge independently of the reviews. A change can be approved
    # by people and still be refused by a machine.
    if blocking := blocking_checks(change_request):
        return BranchActionIndicator(False, f'Required checks are not passing: {", ".join(blocking)}.')

    return BranchActionIndicator(True)


def blocking_checks(change_request):
    """
    Return a description of every required check which is not passing.

    A check registered after this change request was created has no stored row yet. Such a
    check is treated as not run, and therefore blocking: reading the stored rows alone would
    let a request merge past a check nobody had run.
    """
    from netbox_change_control.checks import expected_checks

    rows = {check.name: check for check in change_request.checks.all()}
    expected = expected_checks(change_request)
    blocking = []

    for name, (label, required) in expected.items():
        if not required:
            continue
        row = rows.get(name)
        if row is None:
            blocking.append(f'{label} (not run)')
        elif not row.is_passing:
            blocking.append(f'{row.display_label} ({row.get_status_display().lower()})')

    # A stored required check which is no longer expected still blocks while it is failing,
    # so removing a check from the configuration is a deliberate act, not an accident.
    for name, row in rows.items():
        if name not in expected and row.blocks_merge:
            blocking.append(f'{row.display_label} ({row.get_status_display().lower()})')

    return blocking
