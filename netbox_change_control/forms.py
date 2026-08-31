from core.models import ObjectType
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelBulkEditForm, NetBoxModelFilterSetForm, NetBoxModelForm
from netbox_branching.models import Branch
from users.models import Group, User
from utilities.forms.constants import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.fields import CommentField, DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DateTimePicker, MarkdownWidget

from netbox_change_control.choices import (
    ChangeRequestPriorityChoices,
    ChangeRequestStatusChoices,
    ConditionStateChoices,
    MergeCheckStatusChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.models import ChangeComment, ChangeRequest, MergeCheck, Policy, PolicyRule, Review

# NetBox renders this next to any Markdown-capable field.
MARKDOWN_HELP = _('<i class="mdi mdi-information-outline" aria-hidden="true"></i> Markdown syntax is supported')

__all__ = (
    'ChangeCommentForm',
    'ChangeRequestBulkEditForm',
    'ChangeRequestFilterForm',
    'ChangeRequestForm',
    'MergeCheckFilterForm',
    'MergeCheckForm',
    'PolicyBulkEditForm',
    'PolicyFilterForm',
    'PolicyForm',
    'PolicyRuleBulkEditForm',
    'PolicyRuleFilterForm',
    'PolicyRuleForm',
    'ReviewEditForm',
    'ReviewFilterForm',
    'ReviewForm',
)


class PolicyForm(NetBoxModelForm):
    object_types = DynamicModelMultipleChoiceField(
        queryset=ObjectType.objects.all(),
        required=False,
        label=_('Object types'),
    )
    checks = forms.MultipleChoiceField(
        required=False,
        label=_('Registered checks'),
        help_text=_('Opt-in checks to require wherever this policy applies.'),
    )
    external_checks = forms.CharField(
        required=False,
        label=_('Reported checks'),
        help_text=_(
            'Names reported from outside NetBox, comma separated. '
            'Each is created as pending and blocks the merge until a result is reported.'
        ),
    )
    comments = CommentField()

    fieldsets = (
        FieldSet('name', 'description', 'enabled', 'weight', name=_('Policy')),
        FieldSet('object_types', 'conditions', 'condition_state', name=_('Scope')),
        FieldSet('checks', 'external_checks', name=_('Checks')),
        FieldSet('tags', name=_('Tags')),
    )

    class Meta:
        model = Policy
        fields = (
            'name',
            'description',
            'enabled',
            'weight',
            'object_types',
            'conditions',
            'condition_state',
            'checks',
            'comments',
            'tags',
        )
        widgets = {  # noqa: RUF012
            'conditions': forms.Textarea(attrs={'rows': 6, 'class': 'font-monospace'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # The list is built here rather than at class definition, because a check may be
        # registered by another plugin and the registry is only complete once every plugin
        # has loaded.
        from netbox_change_control.checks import CheckScope, get_registered_checks

        registered = {c.name: c for c in get_registered_checks().values() if c.scope == CheckScope.POLICY}
        stored = set(self.instance.checks or []) if self.instance else set()

        self.fields['checks'].choices = [
            (name, f'{check.label} ({name})') for name, check in sorted(registered.items())
        ]

        # Every stored name belongs to exactly one field: the picker if it is a registered
        # check, the text box otherwise. A name whose check has since been unregistered moves
        # to the text box rather than disappearing, which is also what it now behaves like.
        self.initial['checks'] = sorted(stored & set(registered))
        self.initial['external_checks'] = ', '.join(sorted(stored - set(registered)))

        if not registered:
            self.fields['checks'].help_text = _(
                'No opt-in checks are registered. See the documentation on writing your own.'
            )

    def clean(self):
        """
        Fold the two fields back into the single list the model stores.
        """
        super().clean()
        selected = self.cleaned_data.get('checks') or []
        external = [name.strip() for name in (self.cleaned_data.get('external_checks') or '').split(',')]
        self.cleaned_data['checks'] = sorted(set(selected) | {name for name in external if name})
        return self.cleaned_data


class PolicyRuleForm(NetBoxModelForm):
    policy = DynamicModelChoiceField(
        queryset=Policy.objects.all(),
        label=_('Policy'),
    )
    groups = DynamicModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        label=_('Reviewer groups'),
    )
    users = DynamicModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=False,
        label=_('Reviewers'),
    )

    fieldsets = (
        FieldSet('policy', 'name', 'min_reviews', name=_('Rule')),
        FieldSet('groups', 'users', name=_('Who may approve')),
    )

    class Meta:
        model = PolicyRule
        fields = ('policy', 'name', 'min_reviews', 'groups', 'users', 'tags')

    def clean(self):
        super().clean()
        if not self.cleaned_data.get('groups') and not self.cleaned_data.get('users'):
            raise forms.ValidationError(_('A rule must name at least one reviewer group or one reviewer.'))


class ChangeRequestForm(NetBoxModelForm):
    branch = DynamicModelChoiceField(
        queryset=Branch.objects.all(),
        label=_('Branch'),
    )
    comments = CommentField()

    fieldsets = (
        FieldSet('branch', 'ref', 'title', 'description', 'priority', name=_('Change request')),
        FieldSet('scheduled_start', 'scheduled_end', 'auto_merge', name=_('Change window')),
        FieldSet('tags', name=_('Tags')),
    )

    class Meta:
        model = ChangeRequest
        fields = (
            'branch',
            'ref',
            'title',
            'description',
            'priority',
            'scheduled_start',
            'scheduled_end',
            'auto_merge',
            'comments',
            'tags',
        )
        widgets = {  # noqa: RUF012
            'scheduled_start': DateTimePicker(),
            'scheduled_end': DateTimePicker(),
        }


class ReviewForm(NetBoxModelForm):
    decision = forms.ChoiceField(
        choices=ReviewDecisionChoices,
        label=_('Decision'),
    )
    comment = forms.CharField(
        required=False,
        widget=MarkdownWidget(attrs={'rows': 4}),
        label=_('Comment'),
        help_text=MARKDOWN_HELP,
    )

    class Meta:
        model = Review
        fields = ('decision', 'comment')


class ReviewEditForm(NetBoxModelForm):
    """
    Full form used by the object edit view.

    `change_request` and `reviewer` are deliberately absent. A review is a statement by one
    person about one change request, so reassigning either would forge somebody else's
    position. Leaving them editable also required the editor to hold `users.view_user` just
    to populate a dropdown they should never touch, which left the field empty for an
    ordinary reviewer editing their own comment.
    """

    comment = forms.CharField(
        required=False,
        widget=MarkdownWidget(attrs={'rows': 6}),
        label=_('Comment'),
        help_text=MARKDOWN_HELP,
    )

    fieldsets = (FieldSet('decision', 'comment', name=_('Review')),)

    class Meta:
        model = Review
        fields = ('decision', 'comment', 'tags')


class ChangeCommentForm(NetBoxModelForm):
    """
    Edit a comment.

    Only the text. `change_request`, `change_diff`, `parent` and `author` are all absent: a
    comment is one person's remark about one changed object, so moving it or reattributing it
    would rewrite a record somebody else is relying on. It is the same reasoning that keeps
    those fields off ReviewEditForm.
    """

    text = forms.CharField(
        widget=MarkdownWidget(attrs={'rows': 6}),
        label=_('Comment'),
        help_text=MARKDOWN_HELP,
    )

    fieldsets = (FieldSet('text', name=_('Comment')),)

    class Meta:
        model = ChangeComment
        fields = ('text', 'tags')


class MergeCheckForm(NetBoxModelForm):
    change_request = DynamicModelChoiceField(
        queryset=ChangeRequest.objects.all(),
        label=_('Change request'),
    )
    status = forms.ChoiceField(choices=MergeCheckStatusChoices, label=_('Status'))

    fieldsets = (
        FieldSet('change_request', 'name', 'label', 'required', name=_('Check')),
        FieldSet('status', 'summary', 'details_url', name=_('Result')),
    )

    class Meta:
        model = MergeCheck
        fields = ('change_request', 'name', 'label', 'required', 'status', 'summary', 'details_url', 'tags')


class PolicyFilterForm(NetBoxModelFilterSetForm):
    model = Policy

    fieldsets = (
        FieldSet('q', 'tag'),
        FieldSet('enabled', 'weight', name=_('Policy')),
        FieldSet('object_type_id', 'has_conditions', 'condition_state', name=_('Scope')),
        FieldSet('required_checks', 'has_checks', name=_('Checks')),
    )

    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    weight = forms.IntegerField(required=False, min_value=0)
    object_type_id = DynamicModelMultipleChoiceField(
        queryset=ObjectType.objects.all(),
        required=False,
        label=_('Object types'),
    )
    has_conditions = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    condition_state = forms.MultipleChoiceField(
        choices=ConditionStateChoices, required=False, label=_('Condition state')
    )
    required_checks = forms.MultipleChoiceField(required=False, label=_('Required checks'))
    has_checks = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer every check any policy actually requires, plus everything registered. A name
        # used by no policy would filter to nothing, and a registered check nobody has chosen
        # yet is still worth being able to search for.
        from netbox_change_control.checks import get_registered_checks

        names = set(get_registered_checks())
        for stored in Policy.objects.values_list('checks', flat=True):
            names.update(stored or [])
        self.fields['required_checks'].choices = [(name, name) for name in sorted(names)]


class PolicyRuleFilterForm(NetBoxModelFilterSetForm):
    model = PolicyRule

    fieldsets = (
        FieldSet('q', 'tag'),
        FieldSet('policy_id', 'min_reviews', name=_('Rule')),
        FieldSet('group_id', 'user_id', 'eligible_for', name=_('Who may approve')),
    )

    policy_id = DynamicModelMultipleChoiceField(queryset=Policy.objects.all(), required=False, label=_('Policy'))
    min_reviews = forms.IntegerField(required=False, min_value=0)
    group_id = DynamicModelMultipleChoiceField(queryset=Group.objects.all(), required=False, label=_('Reviewer groups'))
    user_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Reviewers'))
    eligible_for = DynamicModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=False,
        to_field_name='username',
        label=_('Rules this user may satisfy'),
    )


class ChangeRequestFilterForm(NetBoxModelFilterSetForm):
    model = ChangeRequest

    fieldsets = (
        FieldSet('q', 'tag'),
        FieldSet('ref', 'status', 'priority', 'requester_id', name=_('Request')),
        FieldSet('branch_id', 'branch', 'branch_deleted', name=_('Branch')),
        FieldSet('policy_id', 'reviewer_id', 'has_reviews', name=_('Review')),
        FieldSet('check_status', 'has_open_threads', 'gates_cleared', name=_('Checks')),
        FieldSet('has_conflicts', name=_('Conflicts')),
        FieldSet('has_window', 'auto_merge', name=_('Scheduling')),
    )

    ref = forms.CharField(required=False, label=_('Reference'))
    status = forms.MultipleChoiceField(choices=ChangeRequestStatusChoices, required=False)
    priority = forms.MultipleChoiceField(choices=ChangeRequestPriorityChoices, required=False)
    requester_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Requester'))
    branch_id = DynamicModelMultipleChoiceField(queryset=Branch.objects.all(), required=False, label=_('Branch'))
    branch = forms.CharField(required=False, label=_('Branch name contains'))
    branch_deleted = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    policy_id = DynamicModelMultipleChoiceField(
        queryset=Policy.objects.all(), required=False, label=_('Applied policy')
    )
    reviewer_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Reviewed by'))
    has_reviews = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    check_status = forms.MultipleChoiceField(choices=MergeCheckStatusChoices, required=False, label=_('Check status'))
    has_open_threads = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    has_conflicts = forms.NullBooleanField(
        required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES), label=_('Conflicts with main')
    )
    gates_cleared = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
        label=_('Policies and checks satisfied'),
    )
    has_window = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    auto_merge = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))


class ReviewFilterForm(NetBoxModelFilterSetForm):
    model = Review

    fieldsets = (
        FieldSet('q', 'tag'),
        FieldSet('change_request_id', 'reviewer_id', 'decision', name=_('Review')),
    )

    change_request_id = DynamicModelMultipleChoiceField(
        queryset=ChangeRequest.objects.all(), required=False, label=_('Change request')
    )
    reviewer_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Reviewer'))
    decision = forms.MultipleChoiceField(choices=ReviewDecisionChoices, required=False)


class MergeCheckFilterForm(NetBoxModelFilterSetForm):
    model = MergeCheck

    fieldsets = (
        FieldSet('q', 'tag'),
        FieldSet('change_request_id', 'status', 'required', 'blocks_merge', name=_('Check')),
    )

    change_request_id = DynamicModelMultipleChoiceField(
        queryset=ChangeRequest.objects.all(), required=False, label=_('Change request')
    )
    status = forms.MultipleChoiceField(choices=MergeCheckStatusChoices, required=False)
    required = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    blocks_merge = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))


class PolicyBulkEditForm(NetBoxModelBulkEditForm):
    model = Policy
    description = forms.CharField(max_length=200, required=False)
    enabled = forms.NullBooleanField(required=False)
    weight = forms.IntegerField(min_value=0, required=False)

    nullable_fields = ('description',)


class PolicyRuleBulkEditForm(NetBoxModelBulkEditForm):
    model = PolicyRule
    policy = DynamicModelChoiceField(queryset=Policy.objects.all(), required=False)
    min_reviews = forms.IntegerField(min_value=0, required=False)


class ChangeRequestBulkEditForm(NetBoxModelBulkEditForm):
    """
    `status` is deliberately absent.

    It is a cached view of the policy evaluation, not something to type in. Offering it here
    let anybody holding change_changerequest set a request to Completed, which is terminal:
    the merge gate refuses a completed request and nothing reopens one, so the branch was
    blocked for good. Abandoning and reopening are separate permissioned actions on the
    change request itself.
    """

    model = ChangeRequest
    ref = forms.CharField(max_length=100, required=False, label=_('Reference'))
    priority = forms.ChoiceField(choices=ChangeRequestPriorityChoices, required=False)
    description = forms.CharField(max_length=200, required=False)
    scheduled_start = forms.DateTimeField(required=False, widget=DateTimePicker())
    scheduled_end = forms.DateTimeField(required=False, widget=DateTimePicker())
    auto_merge = forms.NullBooleanField(required=False)

    nullable_fields = ('ref', 'description', 'scheduled_start', 'scheduled_end')
