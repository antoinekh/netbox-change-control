from netbox.api.viewsets import NetBoxModelViewSet

from netbox_change_control import filtersets
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)

from .serializers import (
    ChangeCommentSerializer,
    ChangeRequestSerializer,
    MergeCheckSerializer,
    PolicyRuleSerializer,
    PolicySerializer,
    ReviewSerializer,
)

__all__ = (
    'ChangeCommentViewSet',
    'ChangeRequestViewSet',
    'MergeCheckViewSet',
    'PolicyRuleViewSet',
    'PolicyViewSet',
    'ReviewViewSet',
)


class PolicyViewSet(NetBoxModelViewSet):
    queryset = Policy.objects.prefetch_related('object_types', 'tags')
    serializer_class = PolicySerializer
    filterset_class = filtersets.PolicyFilterSet


class PolicyRuleViewSet(NetBoxModelViewSet):
    queryset = PolicyRule.objects.prefetch_related('groups', 'users', 'tags')
    serializer_class = PolicyRuleSerializer
    filterset_class = filtersets.PolicyRuleFilterSet


class ChangeRequestViewSet(NetBoxModelViewSet):
    queryset = ChangeRequest.objects.prefetch_related('policies', 'tags')
    serializer_class = ChangeRequestSerializer
    filterset_class = filtersets.ChangeRequestFilterSet


class ReviewViewSet(NetBoxModelViewSet):
    queryset = Review.objects.select_related('reviewer', 'change_request')
    serializer_class = ReviewSerializer
    filterset_class = filtersets.ReviewFilterSet


class MergeCheckViewSet(NetBoxModelViewSet):
    queryset = MergeCheck.objects.select_related('change_request')
    serializer_class = MergeCheckSerializer
    filterset_class = filtersets.MergeCheckFilterSet


class ChangeCommentViewSet(NetBoxModelViewSet):
    queryset = ChangeComment.objects.select_related('author', 'change_diff', 'change_request')
    serializer_class = ChangeCommentSerializer
    filterset_class = filtersets.ChangeCommentFilterSet
