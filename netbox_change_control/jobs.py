"""
Background jobs.

The interval is read here, at import time, because NetBox's `system_job` decorator records
it in the registry when the class is defined and `rqworker` schedules from that registry at
start-up. Changing the setting therefore takes effect on the next worker restart.
"""

from django.core.exceptions import ImproperlyConfigured
from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config

__all__ = ('AutoMergeJob',)


def _interval():
    """
    Minutes between automatic merge sweeps.

    The interval bounds how late a change window can fire: an hourly sweep merges a 21:00
    window up to 59 minutes late. Ten minutes is the default, close enough for a normal
    change window without writing a Job row every minute. Lower it for tighter windows, and
    keep every change window longer than it; see automerge.unreliable_window.
    """
    value = get_plugin_config('netbox_change_control', 'auto_merge_interval')
    if type(value) is not int or value < 1:
        raise ImproperlyConfigured(
            'netbox_change_control: auto_merge_interval must be a whole number of minutes, '
            f'1 or greater (got {value!r}).'
        )
    return value


@system_job(interval=_interval())
class AutoMergeJob(JobRunner):
    """
    Merge change requests whose change window has opened.

    Event-driven auto-merge covers the case where the last approval or the last check
    arrives while the window is already open. This job covers the other case: everything was
    ready and the request was only waiting for the clock.
    """

    class Meta:
        name = 'Change control: automatic merges'

    def run(self, *args, **kwargs):
        from netbox_change_control.automerge import run_due_auto_merges

        merged = run_due_auto_merges()
        return f'Merged {merged} change request(s).'
