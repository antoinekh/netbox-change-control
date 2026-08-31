"""
Content injected into netbox-branching's own pages.

A branch and its change request are two halves of one job, and until now they only pointed one
way: the change request page carries the merge button, while the branch page said nothing at
all. Somebody who had just finished working in a branch had to leave it, find another menu,
and search for the request by name. Somebody whose merge was refused was told the reason on
branching's merge form, in plain text, with nothing to click.

This closes the loop. Both hooks are read-only and cheap; neither changes how branching
behaves.
"""

from netbox.plugins import PluginTemplateExtension

__all__ = (
    'BranchChangeRequest',
    'template_extensions',
)


class BranchChangeRequest(PluginTemplateExtension):
    """
    Show a branch its change request, or offer to open one.
    """

    models = ('netbox_branching.branch',)

    def alerts(self):
        from netbox_change_control.models import ChangeRequest

        branch = self.context.get('object')
        if branch is None:
            return ''

        change_request = ChangeRequest.objects.filter(branch=branch).select_related('requester').first()

        if change_request is None:
            # The gate refuses a branch with no change request, so saying so here, next to a
            # button that fixes it, is the difference between a dead end and a next step.
            return self.render(
                'netbox_change_control/inc/branch_no_request.html',
                extra_context={'branch': branch},
            )

        # Evaluated rather than read from the cached column: this is one object on one page,
        # which is exactly where the authoritative answer belongs, and it is what the merge
        # will actually use.
        return self.render(
            'netbox_change_control/inc/branch_change_request.html',
            extra_context={
                'change_request': change_request,
                'evaluation': change_request.evaluate(),
                'blocked_reason': change_request.merge_blocked_reason,
            },
        )


template_extensions = (BranchChangeRequest,)
