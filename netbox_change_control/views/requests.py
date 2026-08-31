from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from netbox.object_actions import AddObject, BulkDelete, BulkEdit, BulkExport
from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from netbox_change_control import events, filtersets, forms, tables
from netbox_change_control.checks import sync_checks
from netbox_change_control.models import ChangeRequest, Review
from netbox_change_control.permissions import ABANDON_PERMISSION, CHANGE_PERMISSION, REOPEN_PERMISSION
from netbox_change_control.policy import refresh_status

from .reviews import review_eligibility

__all__ = (
    'AbandonChangeRequestView',
    'ChangeRequestDeleteView',
    'ChangeRequestEditView',
    'ChangeRequestListView',
    'ChangeRequestReviewsView',
    'ChangeRequestView',
    'ReopenChangeRequestView',
    'ReturnToDraftView',
    'SubmitForReviewView',
    'SubmitReviewView',
)


#
# Change requests
#


class ChangeRequestListView(generic.ObjectListView):
    # See the note on PolicyListView: an action with no route renders a button targeting the
    # string "None".
    actions = (AddObject, BulkExport, BulkEdit, BulkDelete)
    queryset = ChangeRequest.objects.annotate(review_count=Count('reviews'))
    table = tables.ChangeRequestTable
    filterset = filtersets.ChangeRequestFilterSet
    filterset_form = forms.ChangeRequestFilterForm


@register_model_view(ChangeRequest)
class ChangeRequestView(generic.ObjectView):
    queryset = ChangeRequest.objects.all()

    def get_extra_context(self, request, instance):
        can_review, reason = review_eligibility(request, instance)
        evaluation = instance.evaluate()

        # The merge itself belongs to netbox-branching. Surface its form here so an approver
        # does not have to hunt for the branch page. Read it through the model property,
        # which returns None once the branch has been deleted; reaching for
        # instance.branch.can_merge crashes on such a request.
        reviews = list(instance.reviews.select_related('reviewer'))
        stale_ids = {r.pk for r in evaluation.stale}
        for review in reviews:
            review.stale = review.pk in stale_ids

        # Reconcile the stored rows with the registered checks before display. A check
        # registered after this request was created has no row yet, and the gate already
        # treats it as blocking, so the panel must show it rather than hide a blocker.
        sync_checks(instance)
        checks = list(instance.checks.all())
        return {
            'evaluation': evaluation,
            'bindings': instance.policy_bindings.select_related('policy'),
            'reviews': reviews,
            'checks': checks,
            'conflicts': instance.conflicts,
            'reconciled_conflicts': instance.reconciled_conflicts,
            'window_warning': instance.auto_merge_window_warning,
            'checks_blocking': [c for c in checks if c.blocks_merge],
            'can_review': can_review,
            'review_blocked_reason': reason,
            'own_review': instance.reviews.filter(reviewer=request.user).first(),
            'can_merge': instance.is_ready_to_merge,
            'merge_blocked_reason': instance.merge_blocked_reason,
            'can_merge_perm': request.user.has_perm('netbox_branching.merge_branch'),
            'can_submit': instance.can_be_submitted and request.user.has_perm(CHANGE_PERMISSION),
            'can_return_to_draft': instance.can_return_to_draft and request.user.has_perm(CHANGE_PERMISSION),
            'can_abandon': instance.can_be_abandoned and request.user.has_perm(ABANDON_PERMISSION),
            'can_reopen': instance.can_be_reopened and request.user.has_perm(REOPEN_PERMISSION),
        }


@register_model_view(ChangeRequest, 'edit')
class ChangeRequestEditView(generic.ObjectEditView):
    queryset = ChangeRequest.objects.all()
    form = forms.ChangeRequestForm

    def alter_object(self, obj, request, url_args, url_kwargs):
        if not obj.pk:
            obj.requester = request.user
        return obj


@register_model_view(ChangeRequest, 'delete')
class ChangeRequestDeleteView(generic.ObjectDeleteView):
    queryset = ChangeRequest.objects.all()


@register_model_view(ChangeRequest, 'reviews')
class ChangeRequestReviewsView(generic.ObjectChildrenView):
    queryset = ChangeRequest.objects.all()
    child_model = Review
    table = tables.ReviewTable
    filterset = filtersets.ReviewFilterSet
    template_name = 'netbox_change_control/changerequest_reviews.html'
    tab = ViewTab(
        label=_('Reviews'),
        badge=lambda obj: obj.reviews.count(),
        permission='netbox_change_control.view_review',
    )

    def get_children(self, request, parent):
        return parent.reviews.select_related('reviewer')

    def get_extra_context(self, request, instance):
        context = super().get_extra_context(request, instance)
        can_review, reason = review_eligibility(request, instance)
        context.update(
            {
                'can_review': can_review,
                'review_blocked_reason': reason,
                'own_review': instance.reviews.filter(reviewer=request.user).first(),
            }
        )
        return context


class SubmitReviewView(View):
    """
    Record one reviewer's decision, then recompute the change request status.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)

        if not request.user.has_perm('netbox_change_control.add_review'):
            messages.error(request, _('You do not have permission to review change requests.'))
            return redirect(change_request.get_absolute_url())

        form = forms.ReviewForm(request.POST)
        if not form.is_valid():
            for error in form.errors.values():
                messages.error(request, error)
            return redirect(change_request.get_absolute_url())

        # Validate before writing. Writing first and deleting on failure destroyed the
        # reviewer's previous, valid review whenever an edit failed validation, for example
        # switching to "request changes" without a comment.
        review = Review.objects.filter(change_request=change_request, reviewer=request.user).first() or Review(
            change_request=change_request, reviewer=request.user
        )
        review.decision = form.cleaned_data['decision']
        review.comment = form.cleaned_data['comment']

        try:
            review.full_clean()
        except ValidationError as e:
            for error in e.messages:
                messages.error(request, error)
            return redirect(change_request.get_absolute_url())

        # Submitting through the form is a deliberate restatement, so the branch snapshot is
        # refreshed even when the decision is unchanged.
        review.save(refresh_snapshot=True)

        refresh_status(change_request)
        messages.success(request, _('Review recorded.'))
        return redirect(change_request.get_absolute_url())


class AbandonChangeRequestView(View):
    """
    Give up on a change request.

    This exists so `status` does not have to be an editable field. It used to be, on the bulk
    edit form and over the REST API, which meant anybody holding `change_changerequest` could
    set a request to Completed. Completed and Abandoned are terminal, the merge gate refuses a
    completed request, and nothing reopens one, so a slip of the mouse permanently blocked a
    branch from merging with no way back through the interface.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)

        if not request.user.has_perm(ABANDON_PERMISSION):
            messages.error(request, _('You do not have permission to abandon change requests.'))
        elif change_request.abandon():
            messages.success(request, _('Change request abandoned.'))
        else:
            messages.error(
                request,
                _('This change request is %(status)s, so it cannot be abandoned.')
                % {'status': change_request.get_status_display().lower()},
            )

        return redirect(change_request.get_absolute_url())


class ReopenChangeRequestView(View):
    """
    Take an abandoned change request back up.

    Only an abandoned one. A completed request records a merge that actually happened, so
    reopening it would invite a second merge of a branch already in main.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)

        if not request.user.has_perm(REOPEN_PERMISSION):
            messages.error(request, _('You do not have permission to reopen change requests.'))
        elif change_request.reopen():
            messages.success(request, _('Change request reopened.'))
        else:
            messages.error(request, _('Only an abandoned change request can be reopened.'))

        return redirect(change_request.get_absolute_url())


class SubmitForReviewView(View):
    """
    Move a draft change request into review, attaching every matching policy.

    Submitting is an edit to the request, so it needs the permission to change one. It used to
    need nothing at all: every other action view here checks something, and this one let any
    signed-in user push somebody else's draft into review, which attaches its policies and
    announces `change_request_submitted` to every event rule watching for it.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest.objects.restrict(request.user, 'change'), pk=pk)

        if not request.user.has_perm(CHANGE_PERMISSION):
            messages.error(request, _('You do not have permission to submit change requests for review.'))
        elif change_request.submit():
            # Distinct from change_request_review_requested, which fires on every entry into
            # Needs review, including an approval invalidated by a later edit. This one is the
            # author's deliberate act of asking for review.
            events.emit(change_request, events.CHANGE_REQUEST_SUBMITTED)
            messages.success(request, _('Change request submitted for review.'))
        else:
            messages.error(request, _('Only a draft can be submitted for review.'))

        return redirect(change_request.get_absolute_url())


class ReturnToDraftView(View):
    """
    Pull a submitted change request back out of review.

    The counterpart to submitting, and the reversible alternative to abandoning: an author who
    finds more to do, or spots a problem after approval, can take the change off the table and
    put it back when it is ready. A draft is not approved, so the merge gate closes with it.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)

        if not request.user.has_perm(CHANGE_PERMISSION):
            messages.error(request, _('You do not have permission to change change requests.'))
        elif change_request.return_to_draft():
            messages.success(request, _('Change request returned to draft.'))
        else:
            messages.error(
                request,
                _('This change request is %(status)s, so it cannot be returned to draft.')
                % {'status': change_request.get_status_display().lower()},
            )

        return redirect(change_request.get_absolute_url())
