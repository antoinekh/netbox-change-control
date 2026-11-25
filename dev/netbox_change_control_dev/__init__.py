"""
Development-only companion to netbox-change-control.

It holds the seed commands, which build demo data and screenshot states. Those exist to make
a local stack usable in one command; they have no place in a production install, so they live
here and are not part of the published package.

The development stack in dev/stack/ puts dev/ on PYTHONPATH and enables it.
"""

import os

from netbox.plugins import PluginConfig

# Where this NetBox answers in a browser, for the links the seed commands print.
BASE_URL = os.environ.get('NETBOX_URL', 'http://localhost:8890').rstrip('/')

__version__ = '0.1.0'


class ChangeControlDevConfig(PluginConfig):
    name = 'netbox_change_control_dev'
    verbose_name = 'Change Control (development helpers)'
    description = 'Seed commands for a local netbox-change-control stack. Not for production.'
    version = __version__
    author = 'Antoine Keranflech'
    base_url = 'change-control-dev'

    def ready(self):
        super().ready()

        # Registered with the policy scope, so they appear in the Registered checks list on a
        # policy rather than applying to every change request.
        from netbox_change_control.checks import CheckScope, register_check

        from .checks import change_is_small, description_is_written

        register_check(
            'change-is-small',
            'Change is small enough to review',
            change_is_small,
            scope=CheckScope.POLICY,
        )
        register_check(
            'description-is-written',
            'Description explains the change',
            description_is_written,
            required=False,
            scope=CheckScope.POLICY,
        )


config = ChangeControlDevConfig
