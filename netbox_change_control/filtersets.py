import django_filters
from core.models import ObjectType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from netbox.filtersets import NetBoxModelFilterSet
from netbox_branching.models import Branch
from users.models import Group, User
from utilities.filters import MultiValueCharFilter

from netbox_change_control.choices import (
    ChangeRequestPriorityChoices,
    ChangeRequestStatusChoices,
    ConditionStateChoices,
    MergeCheckStatusChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)

__all__ = (
    'ChangeCommentFilterSet',
    'ChangeRequestFilterSet',
    'MergeCheckFilterSet',
    'PolicyFilterSet',
    'PolicyRuleFilterSet',
    'ReviewFilterSet',
)

# Every filterset below aims to cover each column its table can display, so a column a user
# can see is a column they can filter on. `q`, `tag`, `created` and `last_updated` come from
# NetBoxModelFilterSet and are not repeated.


class PolicyFilterSet(NetBoxModelFilterSet):
    object_type_id = django_filters.ModelMultipleChoiceFilter(
        field_name='object_types',
        queryset=ObjectType.objects.all(),
        label=_('Object type (ID)'),
    )
    object_type = django_filters.CharFilter(
        method='filter_object_type',
        label=_('Object type (app.model)'),
    )
    condition_state = django_filters.MultipleChoiceFilter(
        choices=ConditionStateChoices,
        label=_('Condition state'),
    )
    has_conditions = django_filters.BooleanFilter(
        method='filter_has_conditions',
        label=_('Has conditions'),
    )
    required_checks = MultiValueCharFilter(
        method='filter_required_checks',
        label=_('Required checks'),
    )
    has_checks = django_filters.BooleanFilter(
        method='filter_has_checks',
        label=_('Requires any check'),
    )

    class Meta:
        model = Policy
        fields = ('id', 'name', 'enabled', 'weight', 'description')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))

    def filter_object_type(self, queryset, name, value):
        """
        Match `dcim.device`, or `dcim` to mean every model in that app.
        """
        if not value.strip():
            return queryset
        app_label, _sep, model = value.partition('.')
        query = Q(object_types__app_label__iexact=app_label)
        if model:
            query &= Q(object_types__model__iexact=model)
        return queryset.filter(query).distinct()

    def filter_has_conditions(self, queryset, name, value):
        return queryset.exclude(conditions__isnull=value)

    def filter_required_checks(self, queryset, name, value):
        """
        Policies requiring any of the named checks.

        `contains` needs a list, and several names are an OR rather than an AND: the question
        being asked is "which policies bring this check in", not "which require all of these".
        """
        if not value:
            return queryset
        query = Q()
        for check in value:
            if check:
                query |= Q(checks__contains=[check])
        return queryset.filter(query).distinct() if query else queryset

    def filter_has_checks(self, queryset, name, value):
        return queryset.exclude(checks=[]) if value else queryset.filter(checks=[])


class PolicyRuleFilterSet(NetBoxModelFilterSet):
    policy_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Policy.objects.all(),
        label=_('Policy (ID)'),
    )
    policy = django_filters.ModelMultipleChoiceFilter(
        field_name='policy__name',
        queryset=Policy.objects.all(),
        to_field_name='name',
        label=_('Policy (name)'),
    )
    group_id = django_filters.ModelMultipleChoiceFilter(
        field_name='groups',
        queryset=Group.objects.all(),
        label=_('Reviewer group (ID)'),
    )
    group = django_filters.ModelMultipleChoiceFilter(
        field_name='groups__name',
        queryset=Group.objects.all(),
        to_field_name='name',
        label=_('Reviewer group (name)'),
    )
    user_id = django_filters.ModelMultipleChoiceFilter(
        field_name='users',
        queryset=User.objects.all(),
        label=_('Reviewer (ID)'),
    )
    user = django_filters.ModelMultipleChoiceFilter(
        field_name='users__username',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Reviewer (username)'),
    )
    eligible_for = django_filters.ModelMultipleChoiceFilter(
        method='filter_eligible_for',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Rules this user may satisfy'),
    )

    class Meta:
        model = PolicyRule
        fields = ('id', 'name', 'min_reviews')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(policy__name__icontains=value))

    def filter_eligible_for(self, queryset, name, value):
        """
        A user satisfies a rule if they are named on it or belong to one of its groups.
        """
        if not value:
            return queryset
        query = Q()
        for user in value:
            query |= Q(users=user) | Q(groups__in=user.groups.all())
        return queryset.filter(query).distinct()


class ChangeRequestFilterSet(NetBoxModelFilterSet):
    status = django_filters.MultipleChoiceFilter(choices=ChangeRequestStatusChoices)
    priority = django_filters.MultipleChoiceFilter(choices=ChangeRequestPriorityChoices)
    branch_id = django_filters.ModelMultipleChoiceFilter(
        field_name='branch',
        queryset=Branch.objects.all(),
        label=_('Branch (ID)'),
    )
    branch = django_filters.CharFilter(
        field_name='branch_name',
        lookup_expr='icontains',
        label=_('Branch (name)'),
    )
    ref = MultiValueCharFilter(
        lookup_expr='icontains',
        label=_('Reference'),
    )
    branch_deleted = django_filters.BooleanFilter(
        field_name='branch',
        lookup_expr='isnull',
        label=_('Branch deleted'),
    )
    requester_id = django_filters.ModelMultipleChoiceFilter(
        field_name='requester',
        queryset=User.objects.all(),
        label=_('Requester (ID)'),
    )
    requester = django_filters.ModelMultipleChoiceFilter(
        field_name='requester__username',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Requester (username)'),
    )
    policy_id = django_filters.ModelMultipleChoiceFilter(
        field_name='policies',
        queryset=Policy.objects.all(),
        label=_('Policy (ID)'),
    )
    policy = django_filters.ModelMultipleChoiceFilter(
        field_name='policies__name',
        queryset=Policy.objects.all(),
        to_field_name='name',
        label=_('Policy (name)'),
    )
    reviewer_id = django_filters.ModelMultipleChoiceFilter(
        field_name='reviews__reviewer',
        queryset=User.objects.all(),
        label=_('Reviewed by (ID)'),
        distinct=True,
    )
    reviewer = django_filters.ModelMultipleChoiceFilter(
        field_name='reviews__reviewer__username',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Reviewed by (username)'),
        distinct=True,
    )
    has_reviews = django_filters.BooleanFilter(
        method='filter_has_reviews',
        label=_('Has reviews'),
    )
    has_open_threads = django_filters.BooleanFilter(
        method='filter_has_open_threads',
        label=_('Has unresolved comment threads'),
    )
    check_status = django_filters.MultipleChoiceFilter(
        field_name='checks__status',
        choices=MergeCheckStatusChoices,
        label=_('Check status'),
        distinct=True,
    )
    scheduled_start = django_filters.DateTimeFromToRangeFilter(
        label=_('Window opens (range)'),
    )
    scheduled_end = django_filters.DateTimeFromToRangeFilter(
        label=_('Window closes (range)'),
    )
    has_window = django_filters.BooleanFilter(
        method='filter_has_window',
        label=_('Has a change window'),
    )
    has_conflicts = django_filters.BooleanFilter(
        field_name='cached_conflicted',
        label=_('Conflicts with main'),
    )
    gates_cleared = django_filters.BooleanFilter(
        field_name='cached_gates_cleared',
        label=_('Policies and checks satisfied'),
    )

    class Meta:
        model = ChangeRequest
        fields = ('id', 'ref', 'title', 'description', 'auto_merge')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(ref__icontains=value)
            | Q(title__icontains=value)
            | Q(description__icontains=value)
            | Q(branch_name__icontains=value)
        )

    def filter_has_reviews(self, queryset, name, value):
        return queryset.filter(reviews__isnull=not value).distinct()

    def filter_has_open_threads(self, queryset, name, value):
        open_threads = Q(change_comments__parent__isnull=True, change_comments__resolved=False)
        if value:
            return queryset.filter(open_threads).distinct()
        return queryset.exclude(open_threads).distinct()

    def filter_has_window(self, queryset, name, value):
        windowed = Q(scheduled_start__isnull=False) | Q(scheduled_end__isnull=False)
        return queryset.filter(windowed) if value else queryset.exclude(windowed)


class ReviewFilterSet(NetBoxModelFilterSet):
    decision = django_filters.MultipleChoiceFilter(choices=ReviewDecisionChoices)
    change_request_id = django_filters.ModelMultipleChoiceFilter(
        field_name='change_request',
        queryset=ChangeRequest.objects.all(),
        label=_('Change request (ID)'),
    )
    reviewer_id = django_filters.ModelMultipleChoiceFilter(
        field_name='reviewer',
        queryset=User.objects.all(),
        label=_('Reviewer (ID)'),
    )
    reviewer = django_filters.ModelMultipleChoiceFilter(
        field_name='reviewer__username',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Reviewer (username)'),
    )

    class Meta:
        model = Review
        fields = ('id', 'comment')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(comment__icontains=value) | Q(change_request__title__icontains=value))


class MergeCheckFilterSet(NetBoxModelFilterSet):
    status = django_filters.MultipleChoiceFilter(choices=MergeCheckStatusChoices)
    change_request_id = django_filters.ModelMultipleChoiceFilter(
        field_name='change_request',
        queryset=ChangeRequest.objects.all(),
        label=_('Change request (ID)'),
    )
    blocks_merge = django_filters.BooleanFilter(
        method='filter_blocks_merge',
        label=_('Blocking the merge'),
    )

    class Meta:
        model = MergeCheck
        fields = ('id', 'name', 'label', 'required', 'summary')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(label__icontains=value) | Q(summary__icontains=value))

    def filter_blocks_merge(self, queryset, name, value):
        """
        A required check in any state other than passed or skipped blocks the merge.
        """
        blocking = Q(required=True) & ~Q(status__in=MergeCheckStatusChoices.PASSING)
        return queryset.filter(blocking) if value else queryset.exclude(blocking)


class ChangeCommentFilterSet(NetBoxModelFilterSet):
    change_request_id = django_filters.ModelMultipleChoiceFilter(
        field_name='change_request',
        queryset=ChangeRequest.objects.all(),
        label=_('Change request (ID)'),
    )
    author_id = django_filters.ModelMultipleChoiceFilter(
        field_name='author',
        queryset=User.objects.all(),
        label=_('Author (ID)'),
    )
    author = django_filters.ModelMultipleChoiceFilter(
        field_name='author__username',
        queryset=User.objects.all(),
        to_field_name='username',
        label=_('Author (username)'),
    )
    is_thread = django_filters.BooleanFilter(
        field_name='parent',
        lookup_expr='isnull',
        label=_('Is a thread, not a reply'),
    )

    class Meta:
        model = ChangeComment
        fields = ('id', 'change_diff_id', 'parent_id', 'resolved', 'change_label')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(text__icontains=value) | Q(change_label__icontains=value))
