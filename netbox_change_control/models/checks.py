from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel

from netbox_change_control.choices import MergeCheckStatusChoices

__all__ = ('MergeCheck',)


class MergeCheck(NetBoxModel):
    """
    The result of one pre-merge check against a change request.

    Checks work like commit status checks on a hosted git service. A check is either run
    in-process by a registered function, or reported by an external system through the REST
    API. A required check which is not passing blocks the merge, independently of reviews.
    """

    change_request = models.ForeignKey(
        to='netbox_change_control.ChangeRequest',
        on_delete=models.CASCADE,
        related_name='checks',
        verbose_name=_('change request'),
    )
    name = models.CharField(
        verbose_name=_('name'),
        max_length=100,
        help_text=_('Stable identifier, for example "config-render". Reported results key on this.'),
    )
    label = models.CharField(
        verbose_name=_('label'),
        max_length=200,
        blank=True,
        help_text=_('Human readable name shown in the interface.'),
    )
    status = models.CharField(
        verbose_name=_('status'),
        max_length=32,
        choices=MergeCheckStatusChoices,
        default=MergeCheckStatusChoices.PENDING,
    )
    required = models.BooleanField(
        verbose_name=_('required'),
        default=True,
        help_text=_('A required check must pass before the branch can merge.'),
    )
    summary = models.CharField(
        verbose_name=_('summary'),
        max_length=500,
        blank=True,
    )
    details_url = models.URLField(
        verbose_name=_('details URL'),
        blank=True,
        help_text=_('Optional link to a build log or report.'),
    )
    completed = models.DateTimeField(
        verbose_name=_('completed'),
        null=True,
        blank=True,
    )

    # What a check run or a report writes; everything else on the row belongs to the plugin.
    RESULT_FIELDS = ('status', 'summary', 'details_url')

    class Meta:
        ordering = ('change_request', 'name')
        constraints = (
            models.UniqueConstraint(
                fields=('change_request', 'name'),
                name='%(app_label)s_%(class)s_unique_request_check',
            ),
        )
        verbose_name = _('merge check')
        verbose_name_plural = _('merge checks')

    def __str__(self):
        return f'{self.display_label} ({self.get_status_display()})'

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:mergecheck', args=[self.pk])

    def get_status_color(self):
        return MergeCheckStatusChoices.colors.get(self.status)

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        """
        Remember the result this row was loaded with, so `save()` can tell a new result from an
        incidental write. `**kwargs` carries the arguments Django adds between releases.
        """
        instance = super().from_db(db, field_names, values, **kwargs)
        instance._loaded_result = instance.result
        return instance

    def save(self, *args, **kwargs):
        """
        Date the result whenever it moves, whoever wrote it.

        `completed` is when the check reached its current result, and is empty while it waits
        for one, so a reported result and a result from a run read the same way.
        """
        if self._state.adding or getattr(self, '_loaded_result', None) != self.result:
            self.completed = timezone.now() if self.status in MergeCheckStatusChoices.FINISHED else None
            if (update_fields := kwargs.get('update_fields')) is not None:
                kwargs['update_fields'] = {*update_fields, 'completed'}
        super().save(*args, **kwargs)
        self._loaded_result = self.result

    def clean(self):
        """
        Refuse a result written by hand when only the check run may write it, or when the request
        is finished and its results are part of its record.

        Every writer except the check run goes through here: the edit form and the REST API.
        """
        super().clean()
        loaded = getattr(self, '_loaded_result', None)
        if self._state.adding or loaded is None or loaded == self.result:
            return
        changed = [field for field, old in zip(self.RESULT_FIELDS, loaded, strict=True) if getattr(self, field) != old]
        if not self.is_reported:
            message = _('%(check)s runs inside NetBox, so its result cannot be set by hand.') % {
                'check': self.display_label
            }
        elif not self.change_request.is_open:
            message = _('This change request is %(status)s, so its check results are kept as they are.') % {
                'status': self.change_request.get_status_display().lower()
            }
        else:
            return
        raise ValidationError(dict.fromkeys(changed, message))

    @property
    def result(self):
        return tuple(getattr(self, field) for field in self.RESULT_FIELDS)

    @property
    def is_reported(self):
        """
        Whether a system outside the plugin reports this check, rather than a registered function.
        """
        from netbox_change_control.checks import get_registered_checks

        return self.name not in get_registered_checks()

    @property
    def display_label(self):
        return self.label or self.name

    @property
    def is_passing(self):
        return self.status in MergeCheckStatusChoices.PASSING

    @property
    def blocks_merge(self):
        return self.required and not self.is_passing
