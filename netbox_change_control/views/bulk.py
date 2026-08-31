from netbox.object_actions import BulkDelete, BulkExport
from netbox.views import generic

from netbox_change_control import filtersets, forms, tables
from netbox_change_control.models import ChangeRequest, Policy, PolicyRule, Review

__all__ = (
    'ChangeRequestBulkDeleteView',
    'ChangeRequestBulkEditView',
    'PolicyBulkDeleteView',
    'PolicyBulkEditView',
    'PolicyRuleBulkDeleteView',
    'PolicyRuleBulkEditView',
    'ReviewBulkDeleteView',
    'ReviewListView',
)


#
# Bulk views
#
# NetBox list views always render the "Edit Selected" and "Delete Selected" actions. Without
# these registered routes the buttons resolve to a broken URL, so every model which has a
# list view needs them.
#


class PolicyBulkEditView(generic.BulkEditView):
    queryset = Policy.objects.all()
    filterset = filtersets.PolicyFilterSet
    table = tables.PolicyTable
    form = forms.PolicyBulkEditForm


class PolicyBulkDeleteView(generic.BulkDeleteView):
    queryset = Policy.objects.all()
    filterset = filtersets.PolicyFilterSet
    table = tables.PolicyTable


class PolicyRuleBulkEditView(generic.BulkEditView):
    queryset = PolicyRule.objects.all()
    filterset = filtersets.PolicyRuleFilterSet
    table = tables.PolicyRuleTable
    form = forms.PolicyRuleBulkEditForm


class PolicyRuleBulkDeleteView(generic.BulkDeleteView):
    queryset = PolicyRule.objects.all()
    filterset = filtersets.PolicyRuleFilterSet
    table = tables.PolicyRuleTable


class ChangeRequestBulkEditView(generic.BulkEditView):
    queryset = ChangeRequest.objects.all()
    filterset = filtersets.ChangeRequestFilterSet
    table = tables.ChangeRequestTable
    form = forms.ChangeRequestBulkEditForm


class ChangeRequestBulkDeleteView(generic.BulkDeleteView):
    queryset = ChangeRequest.objects.all()
    filterset = filtersets.ChangeRequestFilterSet
    table = tables.ChangeRequestTable


class ReviewListView(generic.ObjectListView):
    # A review is written by submitting one, not by adding a row, and there is no bulk edit
    # for it. Offering either rendered a button with a "None" target.
    actions = (BulkExport, BulkDelete)
    queryset = Review.objects.select_related('reviewer', 'change_request')
    table = tables.ReviewTable
    filterset = filtersets.ReviewFilterSet
    filterset_form = forms.ReviewFilterForm


class ReviewBulkDeleteView(generic.BulkDeleteView):
    queryset = Review.objects.all()
    filterset = filtersets.ReviewFilterSet
    table = tables.ReviewTable
