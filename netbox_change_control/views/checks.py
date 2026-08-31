from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from netbox.object_actions import BulkDelete, BulkExport
from netbox.views import generic
from utilities.views import register_model_view

from netbox_change_control import filtersets, forms, tables
from netbox_change_control.checks import run_checks
from netbox_change_control.models import ChangeRequest, MergeCheck

__all__ = (
    'MergeCheckBulkDeleteView',
    'MergeCheckDeleteView',
    'MergeCheckEditView',
    'MergeCheckListView',
    'MergeCheckView',
    'RunChecksView',
)


#
# Merge checks
#


class MergeCheckListView(generic.ObjectListView):
    # A check row is created by the plugin and reported on through the REST API. There is no
    # add or bulk edit route, so neither is offered.
    actions = (BulkExport, BulkDelete)
    queryset = MergeCheck.objects.select_related('change_request')
    table = tables.MergeCheckTable
    filterset = filtersets.MergeCheckFilterSet
    filterset_form = forms.MergeCheckFilterForm


@register_model_view(MergeCheck)
class MergeCheckView(generic.ObjectView):
    queryset = MergeCheck.objects.select_related('change_request')


@register_model_view(MergeCheck, 'edit')
class MergeCheckEditView(generic.ObjectEditView):
    queryset = MergeCheck.objects.all()
    form = forms.MergeCheckForm


@register_model_view(MergeCheck, 'delete')
class MergeCheckDeleteView(generic.ObjectDeleteView):
    queryset = MergeCheck.objects.all()


class MergeCheckBulkDeleteView(generic.BulkDeleteView):
    queryset = MergeCheck.objects.all()
    filterset = filtersets.MergeCheckFilterSet
    table = tables.MergeCheckTable


class RunChecksView(View):
    """
    Re-run the registered checks for one change request on demand.
    """

    def post(self, request, pk):
        change_request = get_object_or_404(ChangeRequest, pk=pk)
        if not request.user.has_perm('netbox_change_control.change_mergecheck'):
            messages.error(request, _('You do not have permission to run checks.'))
            return redirect(change_request.get_absolute_url())

        run_checks(change_request)
        messages.success(request, _('Checks re-run.'))
        return redirect(change_request.get_absolute_url())
