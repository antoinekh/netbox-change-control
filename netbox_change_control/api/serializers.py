from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.permissions import may_rewrite

__all__ = (
    'ChangeCommentSerializer',
    'ChangeRequestSerializer',
    'MergeCheckSerializer',
    'PolicyRuleSerializer',
    'PolicySerializer',
    'ReviewSerializer',
)


def _changed_fields(instance, attrs):
    """
    The submitted fields whose value differs from the stored one.

    A client may send the whole object back with one field changed, so a field is judged by its
    value, not by its presence.
    """
    changed = set()
    for name, value in attrs.items():
        if name == 'tags':
            if {tag.pk for tag in value} != set(instance.tags.values_list('pk', flat=True)):
                changed.add(name)
        elif getattr(instance, name, None) != value:
            changed.add(name)
    return changed


class PolicySerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:policy-detail')

    class Meta:
        model = Policy
        fields = (
            'id',
            'url',
            'display',
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
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name')


class PolicyRuleSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:policyrule-detail')
    policy = PolicySerializer(nested=True)

    class Meta:
        model = PolicyRule
        fields = (
            'id',
            'url',
            'display',
            'policy',
            'name',
            'min_reviews',
            'groups',
            'users',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'min_reviews')


class ChangeRequestSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:changerequest-detail')
    approved = serializers.BooleanField(source='is_approved', read_only=True)
    branch_name = serializers.CharField(read_only=True)
    branch_deleted = serializers.BooleanField(read_only=True)
    ready_to_merge = serializers.BooleanField(source='is_ready_to_merge', read_only=True)
    # Derived from the policy evaluation, so it is reported and never set. A writable status
    # let a caller mark a request Completed, which is terminal and blocks its branch from ever
    # merging. Use the abandon and reopen actions for the two transitions a person makes.
    status = serializers.CharField(read_only=True)
    merge_blocked_reason = serializers.CharField(read_only=True)
    has_conflicts = serializers.BooleanField(read_only=True)

    class Meta:
        model = ChangeRequest
        fields = (
            'id',
            'url',
            'display',
            'ref',
            'branch',
            'branch_name',
            'branch_deleted',
            'title',
            'description',
            'status',
            'priority',
            'requester',
            'approved',
            'ready_to_merge',
            'merge_blocked_reason',
            'has_conflicts',
            'scheduled_start',
            'scheduled_end',
            'auto_merge',
            'comments',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'ref', 'title', 'status')


class ReviewSerializer(NetBoxModelSerializer):
    """
    A review is a personal statement, so the API records it as the caller and never as
    somebody else.

    `reviewer` is read-only and defaults to the requesting user. Leaving it writable let any
    token holding `add_review` post an approval attributed to a colleague, which defeats the
    merge gate with one request. The object form does not offer the field for the same reason.
    """

    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:review-detail')
    reviewer = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Review
        fields = (
            'id',
            'url',
            'display',
            'change_request',
            'reviewer',
            'decision',
            'comment',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'decision')

    def validate(self, attrs):
        """
        Attribute a new review to the caller.

        A read-only field carrying a default is not enough, because DRF leaves a read-only
        field out of validated_data entirely. The assignment also has to happen before
        super(), since NetBox's ValidatedModelSerializer builds a model instance from these
        attributes and calls full_clean() on it, which would reject the missing reviewer.
        """
        # `not self.nested` matters: a nested serializer's to_internal_value returns a model
        # instance rather than a dict, and assigning into it raises TypeError.
        if self.instance is None and not self.nested:
            request = self.context.get('request')
            if request is not None:
                attrs['reviewer'] = request.user
        return super().validate(attrs)


class MergeCheckSerializer(NetBoxModelSerializer):
    """
    External systems report a result by PATCHing status, summary and details_url onto an
    existing check.
    """

    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:mergecheck-detail')
    blocks_merge = serializers.BooleanField(read_only=True)
    # A reporter must not decide whether its own check counts. Requiredness comes from the
    # configuration or the policy, and the gate reads it from there rather than from this row,
    # so a writable field here would only mislead.
    required = serializers.BooleanField(read_only=True)
    # The check belongs to the change request which created it. Moving it elsewhere would
    # detach a result from what it was measuring.
    change_request = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = MergeCheck
        fields = (
            'id',
            'url',
            'display',
            'change_request',
            'name',
            'label',
            'status',
            'required',
            'summary',
            'details_url',
            'completed',
            'blocks_merge',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'status')


class ChangeCommentSerializer(NetBoxModelSerializer):
    """
    A comment is a personal statement, so the API records it as the caller and never as
    somebody else.

    `author` is read-only and defaults to the requesting user, for the same reason `reviewer`
    is on ReviewSerializer. Leaving it writable let any token holding `add_changecomment` post
    a comment attributed to a colleague, which is enough to fake a sign-off in the discussion
    a reviewer reads before approving.
    """

    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_change_control-api:changecomment-detail')
    author = serializers.PrimaryKeyRelatedField(read_only=True)

    def validate(self, attrs):
        """
        Attribute a new comment to the caller.

        A read-only field carrying a default is not enough, because DRF leaves a read-only
        field out of validated_data entirely. The assignment also has to happen before
        super(), since NetBox's ValidatedModelSerializer builds a model instance from these
        attributes and calls full_clean() on it, which would reject the missing author.
        """
        # `not self.nested` matters: a nested serializer's to_internal_value returns a model
        # instance rather than a dict, and assigning into it raises TypeError.
        if self.nested:
            return super().validate(attrs)
        request = self.context.get('request')
        if request is not None:
            if self.instance is None:
                attrs['author'] = request.user
            elif not may_rewrite(request.user, self.instance.author_id):
                # Resolving a thread is what `change_changecomment` is for, whoever wrote it.
                if rewritten := _changed_fields(self.instance, attrs) - {'resolved'}:
                    raise PermissionDenied(
                        f'Only the author may change {", ".join(sorted(rewritten))} on this comment.'
                    )
        return super().validate(attrs)

    class Meta:
        model = ChangeComment
        fields = (
            'id',
            'url',
            'display',
            'change_request',
            'change_diff',
            'parent',
            'author',
            'text',
            'resolved',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'text')
