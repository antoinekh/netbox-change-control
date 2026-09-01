"""
Content injected into netbox-branching's own pages.

A branch and its change request are two halves of one job, and until now they only pointed one
way: the change request page carries the merge button, while the branch page said nothing at
all. Somebody who had just finished working in a branch had to leave it, find another menu,
and search for the request by name. Somebody whose merge was refused was told the reason on
branching's merge form, in plain text, with nothing to click.

This closes the loop. Both hooks are read-only and cheap; neither changes how branching
behaves.

Where it appears is configuration, because the two placements suit different pages and neither
is obviously right until you have looked at both. `branch_page_placement` names them:

    'right_page'  a card in the right-hand column, beside the branch's own cards
    'alerts'      a band across the top of the page, above them

The card is the default, because it reads as part of the page rather than as an interruption.
Naming both shows both, which is how you compare them. An empty list shows neither.
"""

import logging

from netbox.plugins import PluginTemplateExtension, get_plugin_config

__all__ = (
    'ALERTS',
    'PLACEMENTS',
    'RIGHT_PAGE',
    'BranchChangeRequest',
    'configured_placements',
    'template_extensions',
)

logger = logging.getLogger('netbox.plugins.netbox_change_control')

ALERTS = 'alerts'
RIGHT_PAGE = 'right_page'
PLACEMENTS = (ALERTS, RIGHT_PAGE)


def configured_placements():
    """
    The placements the deployment asked for, dropping any name which is not one.

    An unrecognised name is logged and skipped rather than raising, the same way an unknown
    built-in check is, so a typo in configuration does not stop NetBox booting.
    """
    configured = get_plugin_config('netbox_change_control', 'branch_page_placement') or ()
    if isinstance(configured, str):
        configured = (configured,)

    placements = []
    for name in configured:
        if name not in PLACEMENTS:
            logger.warning(
                "Unknown placement '%s' in branch_page_placement. Valid names: %s",
                name,
                ', '.join(PLACEMENTS),
            )
            continue
        placements.append(name)

    return placements


class BranchChangeRequest(PluginTemplateExtension):
    """
    Show a branch its change request, or offer to open one.
    """

    models = ('netbox_branching.branch',)

    def alerts(self):
        return self.render_placement(ALERTS)

    def right_page(self):
        return self.render_placement(RIGHT_PAGE)

    def render_placement(self, placement):
        """
        Render the panel for one placement, or nothing if it is not configured.

        The two placements render the same content in a different frame, so they share their
        wording and differ only in which template wraps it.
        """
        if placement not in configured_placements():
            return ''

        from netbox_change_control.models import ChangeRequest

        branch = self.context.get('object')
        if branch is None:
            return ''

        suffix = '_card' if placement == RIGHT_PAGE else ''
        change_request = ChangeRequest.objects.filter(branch=branch).select_related('requester').first()

        if change_request is None:
            # The gate refuses a branch with no change request, so saying so here, next to a
            # button that fixes it, is the difference between a dead end and a next step.
            return self.render(
                f'netbox_change_control/inc/branch_no_request{suffix}.html',
                extra_context={'branch': branch},
            )

        # Evaluated rather than read from the cached column: this is one object on one page,
        # which is exactly where the authoritative answer belongs, and it is what the merge
        # will actually use.
        return self.render(
            f'netbox_change_control/inc/branch_change_request{suffix}.html',
            extra_context={
                'change_request': change_request,
                'evaluation': change_request.evaluate(),
                'blocked_reason': change_request.merge_blocked_reason,
            },
        )


template_extensions = (BranchChangeRequest,)
