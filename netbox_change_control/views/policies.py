from django.db.models import Count
from netbox.object_actions import AddObject, BulkDelete, BulkEdit, BulkExport
from netbox.views import generic
from utilities.views import register_model_view

from netbox_change_control import filtersets, forms, tables
from netbox_change_control.models import Policy, PolicyRule

__all__ = (
    'PolicyDeleteView',
    'PolicyEditView',
    'PolicyListView',
    'PolicyRuleDeleteView',
    'PolicyRuleEditView',
    'PolicyRuleListView',
    'PolicyRuleView',
    'PolicyView',
)


#
# Policies
#


class PolicyListView(generic.ObjectListView):
    # NetBox offers add, import, export, edit, rename and delete by default, and
    # ObjectAction.get_url swallows the NoReverseMatch for the ones this plugin does not
    # route. The button then renders with a literal "None" target and 404s on click, so each
    # list view states the actions it actually has.
    actions = (AddObject, BulkExport, BulkEdit, BulkDelete)
    queryset = Policy.objects.annotate(rule_count=Count('rules'))
    table = tables.PolicyTable
    filterset = filtersets.PolicyFilterSet
    filterset_form = forms.PolicyFilterForm


@register_model_view(Policy)
class PolicyView(generic.ObjectView):
    queryset = Policy.objects.all()

    def get_extra_context(self, request, instance):
        # `policy` is select_related even though every rule here belongs to `instance`: the
        # table links that column, and a table built by hand never has `configure()` called on
        # it, which is what would otherwise apply NetBox's own column-derived prefetching. A
        # list view gets that for free; this page would issue one query per rule without it.
        rules = PolicyRule.objects.filter(policy=instance).select_related('policy').prefetch_related('groups', 'users')
        return {
            'rules_table': tables.PolicyRuleTable(rules, orderable=False),
        }


@register_model_view(Policy, 'edit')
class PolicyEditView(generic.ObjectEditView):
    queryset = Policy.objects.all()
    form = forms.PolicyForm


@register_model_view(Policy, 'delete')
class PolicyDeleteView(generic.ObjectDeleteView):
    queryset = Policy.objects.all()


#
# Policy rules
#


class PolicyRuleListView(generic.ObjectListView):
    actions = (AddObject, BulkExport, BulkEdit, BulkDelete)
    queryset = PolicyRule.objects.all()
    table = tables.PolicyRuleTable
    filterset = filtersets.PolicyRuleFilterSet
    filterset_form = forms.PolicyRuleFilterForm


@register_model_view(PolicyRule)
class PolicyRuleView(generic.ObjectView):
    queryset = PolicyRule.objects.all()


@register_model_view(PolicyRule, 'edit')
class PolicyRuleEditView(generic.ObjectEditView):
    queryset = PolicyRule.objects.all()
    form = forms.PolicyRuleForm


@register_model_view(PolicyRule, 'delete')
class PolicyRuleDeleteView(generic.ObjectDeleteView):
    queryset = PolicyRule.objects.all()
