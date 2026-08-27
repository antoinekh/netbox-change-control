from django.db import models
from django.urls import reverse
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

    @property
    def display_label(self):
        return self.label or self.name

    @property
    def is_passing(self):
        return self.status in MergeCheckStatusChoices.PASSING

    @property
    def blocks_merge(self):
        return self.required and not self.is_passing
