from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel

__all__ = ('ChangeComment',)


class ChangeComment(NetBoxModel):
    """
    A comment on one changed object inside a branch, or a reply to another such comment.

    This is the fine-grained counterpart to a Review. A Review states a position on the whole
    change request; a comment raises a concern about one specific object.
    """

    change_request = models.ForeignKey(
        to='netbox_change_control.ChangeRequest',
        on_delete=models.CASCADE,
        related_name='change_comments',
        verbose_name=_('change request'),
    )
    change_diff = models.ForeignKey(
        to='netbox_branching.ChangeDiff',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='change_comments',
        verbose_name=_('change'),
        help_text=_('The changed object this comment refers to. Cleared once the branch is gone.'),
    )
    change_label = models.CharField(
        verbose_name=_('change'),
        max_length=200,
        blank=True,
        editable=False,
        help_text=_('Which object this comment was about, kept after the branch is deleted.'),
    )
    parent = models.ForeignKey(
        to='self',
        on_delete=models.CASCADE,
        related_name='replies',
        null=True,
        blank=True,
        verbose_name=_('in reply to'),
    )
    author = models.ForeignKey(
        to='users.User',
        on_delete=models.PROTECT,
        related_name='change_comments',
        verbose_name=_('author'),
    )
    text = models.TextField(
        verbose_name=_('comment'),
    )
    resolved = models.BooleanField(
        verbose_name=_('resolved'),
        default=False,
        help_text=_(
            'Resolved threads no longer count as open concerns. Only a thread can be '
            'resolved; the flag is ignored on a reply.'
        ),
    )

    class Meta:
        ordering = ('change_label', 'created', 'pk')
        verbose_name = _('change comment')
        verbose_name_plural = _('change comments')

    def __str__(self):
        return f'{self.author} on {self.change_label or self.change_diff_id}'

    def save(self, *args, **kwargs):
        # One level of nesting only, enforced here rather than only in clean().
        #
        # clean() reassigns self.parent, but NetBox's ValidatedModelSerializer runs full_clean()
        # on a throw-away copy and keeps only the original attributes, so the flattening was
        # discarded on every REST write. A reply to a reply was then stored as a grandchild,
        # and the Changes tab builds its threads from roots alone, so the comment rendered
        # nowhere at all.
        if self.parent_id and self.parent.parent_id:
            self.parent = self.parent.parent

        # Resolution belongs to a thread, not to an individual comment. Leaving the flag
        # settable on a reply produced rows that looked unresolved while their thread was
        # closed, which is what made the tab badge disagree with the page.
        if self.parent_id:
            self.resolved = False

        # Record what this comment was about. A branch deletion removes the ChangeDiff, and a
        # comment that no longer says which object it concerned is worthless as a record.
        if self.change_diff_id and self.change_diff:
            self.change_label = self.change_diff.object_repr[:200]
            if (update_fields := kwargs.get('update_fields')) is not None:
                kwargs['update_fields'] = {*update_fields, 'change_label'}
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:changerequest_changes', args=[self.change_request_id])

    def clean(self):
        super().clean()

        if self.change_diff_id is None and self.pk is None:
            raise ValidationError({'change_diff': _('A comment must name the change it refers to.')})

        # The change must be one of this request's own. The Changes tab looks the diff up
        # scoped to the branch, so the interface could not cross them, but the REST API took
        # both as plain ids and would happily file a comment on one request against another
        # request's diff. It would then be invisible on the tab it belongs to and counted as
        # an open thread on a request it does not describe.
        if self.change_diff_id and self.change_request_id:
            # Fetch rather than dereference. A stale id raises ChangeDiff.DoesNotExist, which
            # is not a ValidationError and escapes as a server error; a branch deleted
            # concurrently is the realistic way to reach that.
            from netbox_branching.models import ChangeDiff

            branch_id = ChangeDiff.objects.filter(pk=self.change_diff_id).values_list('branch_id', flat=True).first()
            if branch_id is None:
                raise ValidationError({'change_diff': _('That change no longer exists.')})
            if branch_id != self.change_request.branch_id:
                raise ValidationError({'change_diff': _('That change belongs to a different branch.')})

        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({'parent': _('A comment cannot reply to itself.')})
            # One level of nesting only. A reply to a reply joins the same thread, which keeps
            # rendering simple and matches how people actually read a discussion.
            if self.parent.parent_id:
                self.parent = self.parent.parent
            if self.parent.change_diff_id != self.change_diff_id:
                raise ValidationError({'parent': _('A reply must be on the same change as its parent.')})

    @property
    def is_thread_root(self):
        return self.parent_id is None
