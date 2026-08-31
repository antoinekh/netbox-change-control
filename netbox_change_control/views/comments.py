from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from netbox_change_control import forms
from netbox_change_control.diffs import build_change_rows
from netbox_change_control.models import ChangeComment, ChangeRequest

__all__ = (
    'AddChangeCommentView',
    'ChangeCommentDeleteView',
    'ChangeCommentEditView',
    'ChangeRequestChangesView',
    'ResolveChangeCommentView',
)


#
# Changes tab: per-object discussion on the branch diff
#


@register_model_view(ChangeRequest, 'changes')
class ChangeRequestChangesView(generic.ObjectView):
    """
    Show every object the branch touches, each with its own comment thread.

    A Review states a position on the whole request. A comment here raises a concern about
    one specific object, which is what a reviewer needs to give actionable feedback.
    """

    queryset = ChangeRequest.objects.all()
    template_name = 'netbox_change_control/changerequest_changes.html'
    tab = ViewTab(
        label=_('Changes'),
        # Count threads, not comments. Resolution is a property of a thread, so counting
        # every unresolved row also counted replies, including replies on threads that were
        # already resolved. The badge then disagreed with the banner on the page itself.
        badge=lambda obj: obj.change_comments.filter(parent__isnull=True, resolved=False).count() or None,
        permission='netbox_branching.view_changediff',
    )

    def get_extra_context(self, request, instance):
        from netbox_branching.models import ChangeDiff

        diffs = list(ChangeDiff.objects.filter(branch=instance.branch).select_related('object_type'))
        attribute_rows = build_change_rows(diffs)
        comments = instance.change_comments.select_related('author', 'change_diff').order_by('created', 'pk')

        threads = {}
        orphaned = []
        for comment in comments:
            if comment.parent_id is not None:
                continue
            if comment.change_diff_id is None:
                # The branch is gone, so there is no diff row to hang this thread under. It
                # is still part of the record, so it is shown separately rather than dropped.
                orphaned.append({'comment': comment, 'replies': []})
                continue
            threads.setdefault(comment.change_diff_id, []).append(
                {
                    'comment': comment,
                    'replies': [],
                }
            )
        by_id = {thread['comment'].pk: thread for group in (*threads.values(), orphaned) for thread in group}
        for comment in comments:
            if comment.parent_id and (thread := by_id.get(comment.parent_id)):
                thread['replies'].append(comment)

        rows = [
            {
                'diff': diff,
                'attributes': attribute_rows[diff.pk],
                'threads': threads.get(diff.pk, []),
                'open_count': sum(1 for t in threads.get(diff.pk, []) if not t['comment'].resolved),
            }
            for diff in diffs
        ]

        return {
            'rows': rows,
            'orphaned_threads': orphaned,
            'branch_deleted': instance.branch_deleted,
            'can_comment': (instance.is_open and request.user.has_perm('netbox_change_control.add_changecomment')),
            'can_resolve': request.user.has_perm('netbox_change_control.change_changecomment'),
            'open_threads': instance.change_comments.filter(parent__isnull=True, resolved=False).count(),
        }


@register_model_view(ChangeComment, 'edit')
class ChangeCommentEditView(generic.ObjectEditView):
    """
    Edit a comment.

    An ordinary user may edit only their own, for the same reason a review may only be edited
    by its reviewer: a comment is a statement attributed to a person, and rewriting somebody
    else's puts words in their mouth in the record a reviewer reads before approving. A
    superuser may edit any.

    Deleting is not narrowed the same way. That follows NetBox's own model, where a permission
    covers every object of its type unless an object permission constrains it.
    """

    queryset = ChangeComment.objects.all()
    form = forms.ChangeCommentForm

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(author=request.user)


@register_model_view(ChangeComment, 'delete')
class ChangeCommentDeleteView(generic.ObjectDeleteView):
    queryset = ChangeComment.objects.all()


class AddChangeCommentView(View):
    """
    Post a comment on one changed object, or a reply within an existing thread.
    """

    def post(self, request, pk):
        from netbox_branching.models import ChangeDiff

        change_request = get_object_or_404(ChangeRequest, pk=pk)

        if not request.user.has_perm('netbox_change_control.add_changecomment'):
            messages.error(request, _('You do not have permission to comment on changes.'))
            return redirect(change_request.get_absolute_url())

        text = (request.POST.get('text') or '').strip()
        if not text:
            messages.error(request, _('A comment cannot be empty.'))
            return redirect(self._changes_url(change_request))

        diff = get_object_or_404(ChangeDiff, pk=request.POST.get('change_diff'), branch=change_request.branch)
        parent = None
        if parent_id := request.POST.get('parent'):
            parent = ChangeComment.objects.filter(pk=parent_id, change_request=change_request).first()

        comment = ChangeComment(
            change_request=change_request,
            change_diff=diff,
            parent=parent,
            author=request.user,
            text=text,
        )
        try:
            comment.full_clean()
        except ValidationError as e:
            for error in e.messages:
                messages.error(request, error)
            return redirect(self._changes_url(change_request))
        comment.save()

        messages.success(request, _('Comment posted.'))
        return redirect(f'{self._changes_url(change_request)}#comment-{comment.pk}')

    @staticmethod
    def _changes_url(change_request):
        return reverse('plugins:netbox_change_control:changerequest_changes', args=[change_request.pk])


class ResolveChangeCommentView(View):
    """
    Toggle a thread between open and resolved.
    """

    def post(self, request, pk):
        comment = get_object_or_404(ChangeComment, pk=pk, parent__isnull=True)

        if not request.user.has_perm('netbox_change_control.change_changecomment'):
            messages.error(request, _('You do not have permission to resolve threads.'))
        else:
            comment.resolved = not comment.resolved
            comment.save(update_fields=['resolved'])

        return redirect(
            reverse(
                'plugins:netbox_change_control:changerequest_changes',
                args=[comment.change_request_id],
            )
        )
