"""
Dashboard widget listing the change requests waiting on the signed-in user.
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from extras.dashboard.utils import register_widget
from extras.dashboard.widgets import DashboardWidget, WidgetConfigForm

__all__ = ('MyReviewsWidget',)


@register_widget
class MyReviewsWidget(DashboardWidget):
    """
    Shows only requests the user can actually move forward: a rule they are eligible for is
    still short, and they have not already reviewed. This is the same rule the notifications
    use, so the widget and the inbox agree.
    """

    default_title = _('My Reviews')
    description = _('Change requests waiting on your review')
    width = 6
    height = 4

    # Evaluating a change request costs several queries, so the widget stops after this many
    # rather than evaluating an unbounded backlog on every home page load.
    max_requests = 25

    class ConfigForm(WidgetConfigForm):
        pass

    def render(self, request):
        from netbox_change_control.choices import ChangeRequestStatusChoices
        from netbox_change_control.models import ChangeRequest
        from netbox_change_control.notifications import pending_reviewers

        if not request.user.is_authenticated:
            return ''

        # Narrow in SQL first: skip requests this user opened, and those they have already
        # reviewed. Only the survivors need a policy evaluation.
        queryset = (
            ChangeRequest.objects.filter(
                status__in=(
                    ChangeRequestStatusChoices.NEEDS_REVIEW,
                    ChangeRequestStatusChoices.DRAFT,
                )
            )
            .exclude(requester=request.user)
            .exclude(reviews__reviewer=request.user)
            .select_related('branch', 'requester')
            .order_by('-created')[: self.max_requests]
        )

        waiting = [
            change_request
            for change_request in queryset
            if pending_reviewers(change_request).filter(pk=request.user.pk).exists()
        ]

        return render_to_string(
            'netbox_change_control/widgets/my_reviews.html',
            {'change_requests': waiting, 'truncated': len(queryset) == self.max_requests},
        )
