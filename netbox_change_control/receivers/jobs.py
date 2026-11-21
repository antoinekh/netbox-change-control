"""
Track the background job running in this context, so a gate can tell who runs a merge job.
"""

from core.signals import job_end, job_start
from django.dispatch import receiver

from netbox_change_control.permissions import running_job

__all__ = (
    'forget_running_job',
    'track_running_job',
)


@receiver(job_start)
def track_running_job(sender, **kwargs):
    running_job.set(sender)


@receiver(job_end)
def forget_running_job(sender, **kwargs):
    running_job.set(None)
