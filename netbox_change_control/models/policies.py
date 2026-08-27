from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel, PrimaryModel

from netbox_change_control.choices import ConditionStateChoices

__all__ = (
    'Policy',
    'PolicyRule',
)


class Policy(PrimaryModel):
    """
    A named set of approval rules, plus the scope which decides when those rules apply.

    A policy is satisfied only when every one of its rules is satisfied.
    """

    name = models.CharField(
        verbose_name=_('name'),
        max_length=100,
        unique=True,
    )
    enabled = models.BooleanField(
        verbose_name=_('enabled'),
        default=True,
        help_text=_('Disabled policies are never attached to new change requests.'),
    )
    weight = models.PositiveSmallIntegerField(
        verbose_name=_('weight'),
        default=1000,
        help_text=_('Evaluation order. Lower weights are listed first.'),
    )
    object_types = models.ManyToManyField(
        to='core.ObjectType',
        related_name='change_control_policies',
        blank=True,
        verbose_name=_('object types'),
        help_text=_('Apply when the branch touches any of these object types. Leave empty to apply to every branch.'),
    )
    conditions = models.JSONField(
        verbose_name=_('conditions'),
        blank=True,
        null=True,
        help_text=_('An optional NetBox condition set evaluated against each changed object.'),
    )
    condition_state = models.CharField(
        verbose_name=_('condition state'),
        max_length=16,
        choices=ConditionStateChoices,
        default=ConditionStateChoices.EITHER,
        help_text=_(
            'Which side of a change the conditions are evaluated against. The default matches '
            'either side, so a policy guarding active objects also catches one being switched off.'
        ),
    )
    checks = ArrayField(
        base_field=models.CharField(max_length=100),
        verbose_name=_('checks'),
        blank=True,
        default=list,
        help_text=_(
            'Pre-merge checks required only when this policy applies. '
            'A name which is not a registered check is treated as one reported over the REST API.'
        ),
    )

    class Meta:
        ordering = ('weight', 'name')
        permissions = (
            # NetBox resolves a permission as <app_label>.<action>_<model>, so a custom
            # action must end in a real model name or it can never be granted through an
            # object permission. Named to match the commercial product's bypass_policy.
            ('bypass_policy', 'Can write outside a branch while protect_main is enabled'),
        )
        verbose_name = _('policy')
        verbose_name_plural = _('policies')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:policy', args=[self.pk])

    def clean(self):
        super().clean()
        if self.conditions:
            from extras.conditions import ConditionSet, InvalidCondition

            try:
                ConditionSet(self.conditions)
            except (InvalidCondition, ValueError) as e:
                raise ValidationError({'conditions': str(e)}) from e

    @property
    def applies_to_all_object_types(self):
        return not self.object_types.exists()


class PolicyRule(NetBoxModel):
    """
    One approval requirement inside a policy.

    A user is eligible to satisfy this rule when they belong to any of its groups OR are
    named individually. The rule passes once `min_reviews` distinct eligible users have
    approved.
    """

    policy = models.ForeignKey(
        to=Policy,
        on_delete=models.CASCADE,
        related_name='rules',
        verbose_name=_('policy'),
    )
    name = models.CharField(
        verbose_name=_('name'),
        max_length=100,
    )
    min_reviews = models.PositiveSmallIntegerField(
        verbose_name=_('minimum reviews'),
        default=1,
        help_text=_(
            'How many eligible people must approve. Zero means this policy requires no human '
            'approval, leaving the pre-merge checks as the only gate.'
        ),
    )
    groups = models.ManyToManyField(
        to='users.Group',
        related_name='change_control_rules',
        blank=True,
        verbose_name=_('reviewer groups'),
    )
    users = models.ManyToManyField(
        to='users.User',
        related_name='change_control_rules',
        blank=True,
        verbose_name=_('reviewers'),
    )

    class Meta:
        ordering = ('policy', 'name')
        constraints = (
            models.UniqueConstraint(fields=('policy', 'name'), name='%(app_label)s_%(class)s_unique_policy_name'),
        )
        verbose_name = _('policy rule')
        verbose_name_plural = _('policy rules')

    def __str__(self):
        return f'{self.policy}: {self.name}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_change_control:policyrule', args=[self.pk])

    def is_eligible(self, user):
        """
        Return True if `user` may satisfy this rule.
        """
        if not user or not user.is_authenticated:
            return False
        if self.users.filter(pk=user.pk).exists():
            return True
        return self.groups.filter(pk__in=user.groups.values_list('pk', flat=True)).exists()

    def eligible_users(self):
        """
        Return a queryset of every user who may satisfy this rule.
        """
        from users.models import User

        return User.objects.filter(
            models.Q(pk__in=self.users.values_list('pk', flat=True))
            | models.Q(groups__in=self.groups.values_list('pk', flat=True))
        ).distinct()
