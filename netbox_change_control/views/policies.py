from django.db.models import Count
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
    queryset = Policy.objects.annotate(rule_count=Count('rules'))
    table = tables.PolicyTable
    filterset = filtersets.PolicyFilterSet
    filterset_form = forms.PolicyFilterForm


@register_model_view(Policy)
class PolicyView(generic.ObjectView):
    queryset = Policy.objects.all()

    def get_extra_context(self, request, instance):
        rules = PolicyRule.objects.filter(policy=instance).prefetch_related('groups', 'users')
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
