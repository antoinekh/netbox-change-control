from django.utils.translation import gettext_lazy as _
from netbox.views import generic
from utilities.views import register_model_view

from netbox_change_control import forms
from netbox_change_control.models import Review

__all__ = (
    'ReviewDeleteView',
    'ReviewEditView',
    'ReviewView',
    'review_eligibility',
)


# NetBoxTable always renders an edit/delete/changelog actions column, so every model with a
# table needs these routes registered or the table fails to render with NoReverseMatch.


@register_model_view(Review)
class ReviewView(generic.ObjectView):
    queryset = Review.objects.select_related('reviewer', 'change_request')


@register_model_view(Review, 'edit')
class ReviewEditView(generic.ObjectEditView):
    """
    Edit a review.

    A review is a personal statement, so an ordinary user may edit only their own. Without
    this, anyone holding change_review could turn a colleague's "request changes" into an
    approval, which is the whole gate defeated by one form post.
    """

    queryset = Review.objects.all()
    form = forms.ReviewEditForm

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(reviewer=request.user)


@register_model_view(Review, 'delete')
class ReviewDeleteView(generic.ObjectDeleteView):
    queryset = Review.objects.all()


def review_eligibility(request, change_request):
    """
    Return (can_review, reason).

    The reason is rendered in place of the review form, so a hidden form never leaves the
    user guessing why they cannot act.
    """
    if not change_request.is_open:
        return False, _('This change request is %(status)s, so it can no longer be reviewed.') % {
            'status': change_request.get_status_display().lower()
        }
    if change_request.requester_id == request.user.pk:
        return False, _(
            'You opened this change request, so you cannot review it. Sign in as one of the '
            'users listed under "May approve" on the change request page.'
        )
    if not request.user.has_perm('netbox_change_control.add_review'):
        return False, _('You do not have permission to review change requests.')
    return True, ''
