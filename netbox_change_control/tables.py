import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from netbox.tables import NetBoxTable, columns

from netbox_change_control.models import ChangeRequest, MergeCheck, Policy, PolicyRule, Review

__all__ = (
    'ChangeRequestTable',
    'MergeCheckTable',
    'PolicyRuleTable',
    'PolicyTable',
    'ReviewTable',
)


class ConflictsColumn(tables.TemplateColumn):
    """
    Render conflicts the way netbox-branching's own branch list does, so the two pages agree
    at a glance: a red octagon when there are conflicts, the standard placeholder when not.
    """

    template_code = """
    {% if record.cached_conflicted %}
      <span class="text-red"><i class="mdi mdi-alert-octagon"></i></span>
    {% else %}
      {{ ''|placeholder }}
    {% endif %}
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, template_code=self.template_code, **kwargs)


class PolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn(verbose_name=_('Enabled'))
    object_types = columns.ManyToManyColumn(verbose_name=_('Object types'))
    rule_count = columns.LinkedCountColumn(
        viewname='plugins:netbox_change_control:policyrule_list',
        url_params={'policy_id': 'pk'},
        verbose_name=_('Rules'),
    )
    condition_state = columns.ChoiceFieldColumn(verbose_name=_('Condition state'))
    checks = columns.ArrayColumn(verbose_name=_('Required checks'))
    tags = columns.TagColumn(url_name='plugins:netbox_change_control:policy_list')

    class Meta(NetBoxTable.Meta):
        model = Policy
        fields = (
            'pk',
            'id',
            'name',
            'enabled',
            'weight',
            'object_types',
            'rule_count',
            'condition_state',
            'checks',
            'description',
            'tags',
        )
        default_columns = ('name', 'enabled', 'weight', 'object_types', 'rule_count', 'description')


class PolicyRuleTable(NetBoxTable):
    name = tables.Column(linkify=True)
    policy = tables.Column(linkify=True)
    groups = columns.ManyToManyColumn(verbose_name=_('Reviewer groups'))
    users = columns.ManyToManyColumn(verbose_name=_('Reviewers'))

    class Meta(NetBoxTable.Meta):
        model = PolicyRule
        fields = ('pk', 'id', 'name', 'policy', 'min_reviews', 'groups', 'users')
        default_columns = ('name', 'policy', 'min_reviews', 'groups', 'users')


class ChangeRequestTable(NetBoxTable):
    ref = tables.Column(linkify=True, verbose_name=_('Reference'))
    title = tables.Column(linkify=True)
    branch = tables.Column(linkify=True)
    branch_label = tables.Column(
        verbose_name=_('Branch name'),
        orderable=False,
        empty_values=(),
    )
    status = columns.ChoiceFieldColumn()
    priority = columns.ChoiceFieldColumn()
    requester = tables.Column(linkify=True)
    policies = columns.ManyToManyColumn(verbose_name=_('Policies'))
    # No accessor: the name matches the annotation ChangeRequestListView adds, and declaring
    # `reviews__count` instead resolved the related manager and called .count() per row,
    # paying for a query the annotation had already done in bulk.
    review_count = tables.Column(verbose_name=_('Reviews'))
    auto_merge = columns.BooleanColumn(verbose_name=_('Auto merge'))
    # Both read a cached field rather than recomputing per row. Live, they cost about eleven
    # queries each row, which is five hundred for a default page of fifty. Sortable as a
    # result, which the live versions could never be.
    cached_ready_to_merge = columns.BooleanColumn(
        verbose_name=_('Ready to merge'),
        accessor='cached_ready_to_merge',
        order_by='cached_gates_cleared',
    )
    cached_conflicted = ConflictsColumn(
        verbose_name=_('Conflicts'),
        order_by='cached_conflicted',
    )
    tags = columns.TagColumn(url_name='plugins:netbox_change_control:changerequest_list')

    class Meta(NetBoxTable.Meta):
        model = ChangeRequest
        fields = (
            'pk',
            'id',
            'ref',
            'title',
            'branch',
            'branch_label',
            'status',
            'priority',
            'requester',
            'policies',
            'review_count',
            'description',
            'scheduled_start',
            'scheduled_end',
            'auto_merge',
            'cached_ready_to_merge',
            'cached_conflicted',
            'created',
            'tags',
        )
        default_columns = (
            'ref',
            'title',
            'description',
            'branch',
            'status',
            'cached_conflicted',
            'cached_ready_to_merge',
            'priority',
            'requester',
            'policies',
            'created',
        )


class ReviewTable(NetBoxTable):
    reviewer = tables.Column(linkify=True)
    change_request = tables.Column(linkify=True)
    decision = columns.ChoiceFieldColumn()
    comment = columns.MarkdownColumn()

    class Meta(NetBoxTable.Meta):
        model = Review
        fields = ('pk', 'id', 'change_request', 'reviewer', 'decision', 'comment', 'created')
        default_columns = ('change_request', 'reviewer', 'decision', 'comment', 'created')


class MergeCheckTable(NetBoxTable):
    display_label = tables.Column(linkify=True, verbose_name=_('Check'))
    change_request = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    required = columns.BooleanColumn(verbose_name=_('Required'))

    class Meta(NetBoxTable.Meta):
        model = MergeCheck
        fields = (
            'pk',
            'id',
            'display_label',
            'name',
            'change_request',
            'status',
            'required',
            'summary',
            'completed',
        )
        default_columns = ('display_label', 'change_request', 'status', 'required', 'summary', 'completed')
