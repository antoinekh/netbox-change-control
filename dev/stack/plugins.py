"""
NetBox configuration for the development stack, read after the image's own configuration.

It enables netbox-branching, this plugin and the seed plugin in dev/, with the database the
compose file names, so every developer runs the same configuration.
"""

import os

from netbox_branching.utilities import DynamicSchemaDict

PLUGINS = [
    'netbox_change_control',
    # The seed commands, which are not part of the published package.
    'netbox_change_control_dev',
    # netbox-branching must stay last.
    'netbox_branching',
]

PLUGINS_CONFIG = {
    'netbox_branching': {
        # A change request created while a branch is active must still be written to main.
        'exempt_models': ['netbox_change_control.*'],
    },
    'netbox_change_control': {
        # Circuits only, so the seed can show protect_main while the rest of main stays editable.
        'protect_main': True,
        'protect_main_scope': ['circuits.*'],
        'enforce_merge_gate': True,
        # The shipped default, which the documentation screenshots show.
        'branch_page_placement': ['right_page'],
    },
}

# netbox-branching gives each branch its own schema, so it needs its own database wrapper.
DATABASES = DynamicSchemaDict(
    {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ['DB_NAME'],
            'USER': os.environ['DB_USER'],
            'PASSWORD': os.environ['DB_PASSWORD'],
            'HOST': os.environ['DB_HOST'],
            'PORT': '',
            'CONN_MAX_AGE': 300,
        }
    }
)

# A webhook to the receiver in the stack must not go to the proxy that Docker passes in.
PROXY_ROUTERS = ['netbox_change_control_dev.proxy.LocalBypassProxyRouter']

DATABASE_ROUTERS = [
    'netbox_branching.database.BranchAwareRouter',
]
