from typing import ClassVar

from django.shortcuts import get_object_or_404
from netbox.api.authentication import TokenPermissions
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from netbox_change_control import filtersets
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.permissions import ABANDON_PERMISSION, CHANGE_PERMISSION, REOPEN_PERMISSION

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


class ObjectActionPermissions(TokenPermissions):
    """
    Permissions for an action performed *on* an object rather than one that creates one.

    NetBox maps every POST to `add_<model>`, which is right for creating an object and wrong
    here: abandoning a change request is not adding one, and requiring `add_changerequest` to
    give up on somebody else's change is both surprising and too broad.

    The blanket requirement is dropped, and each action states the permission it wants and
    restricts its own queryset to the objects the caller holds that permission on. Everything
    else is inherited, including the check that a write token is not read-only.
    """

    perms_map: ClassVar = {**TokenPermissions.perms_map, 'POST': []}


class ChangeRequestViewSet(NetBoxModelViewSet):
    queryset = ChangeRequest.objects.prefetch_related('policies', 'tags')
    serializer_class = ChangeRequestSerializer
    filterset_class = filtersets.ChangeRequestFilterSet

    def _restricted(self, request, action_name, pk):
        """
        Fetch the object, honouring any constraint on the caller's permission for this action.

        `self.get_object()` cannot be used: BaseViewSet.initial() has already narrowed
        `self.queryset` to what the caller may *add*, which is the wrong question for these.

        A caller holding the permission but constrained away from this object gets a 404,
        which is the NetBox convention for an object-level miss. Not holding it at all is a
        403, raised by the action before it gets here.
        """
        return get_object_or_404(ChangeRequest.objects.restrict(request.user, action_name), pk=pk)

    # `status` is read-only on the serializer because it is derived from the policy
    # evaluation. These are the two transitions a person makes by hand, each behind its own
    # permission, so an integration can still give up on a change or take one back up without
    # the field being writable and Completed being one typo away.

    @action(detail=True, methods=['post'], permission_classes=[ObjectActionPermissions])
    def submit(self, request, pk=None):
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied('You do not have permission to change change requests.')

        change_request = self._restricted(request, 'change', pk)
        if not change_request.submit():
            return Response(
                {'detail': 'Only a draft can be submitted for review.'},
                status=status.HTTP_409_CONFLICT,
            )

        from netbox_change_control import events

        events.emit(change_request, events.CHANGE_REQUEST_SUBMITTED)
        change_request.refresh_from_db()
        return Response(self.get_serializer(change_request).data)

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied('You do not have permission to change change requests.')

        change_request = self._restricted(request, 'change', pk)
        if not change_request.return_to_draft():
            return Response(
                {
                    'detail': (
                        f'A {change_request.get_status_display().lower()} change request cannot be returned to draft.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(self.get_serializer(change_request).data)

    @action(detail=True, methods=['post'], permission_classes=[ObjectActionPermissions])
    def abandon(self, request, pk=None):
        if not request.user.has_perm(ABANDON_PERMISSION):
            raise PermissionDenied('You do not have permission to abandon change requests.')

        change_request = self._restricted(request, 'abandon', pk)
        if not change_request.abandon():
            return Response(
                {'detail': f'A {change_request.get_status_display().lower()} change request cannot be abandoned.'},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(self.get_serializer(change_request).data)

    @action(detail=True, methods=['post'], permission_classes=[ObjectActionPermissions])
    def reopen(self, request, pk=None):
        if not request.user.has_perm(REOPEN_PERMISSION):
            raise PermissionDenied('You do not have permission to reopen change requests.')

        change_request = self._restricted(request, 'reopen', pk)
        if not change_request.reopen():
            return Response(
                {'detail': 'Only an abandoned change request can be reopened.'},
                status=status.HTTP_409_CONFLICT,
            )

        change_request.refresh_from_db()
        return Response(self.get_serializer(change_request).data)


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
