###################################################################
#  Base configuration for running the test suite in CI.           #
#  Not intended for production use.                               #
###################################################################
from netbox_branching.utilities import DynamicSchemaDict

ALLOWED_HOSTS = ['*']

DATABASES = DynamicSchemaDict({
    'default': {
        'NAME': 'netbox',
        'USER': 'netbox',
        'PASSWORD': 'netbox',
        'HOST': 'localhost',
        'PORT': '',
        'CONN_MAX_AGE': 300,
    },
})

DATABASE_ROUTERS = [
    'netbox_branching.database.BranchAwareRouter',
]

# netbox_branching must come last.
PLUGINS = [
    'netbox_change_control',
    'netbox_branching',
]

PLUGINS_CONFIG = {
    'netbox_branching': {
        # Required: change-control records must live in main, never inside a branch.
        'exempt_models': ['netbox_change_control.*'],
    },
    'netbox_change_control': {
        # Left at their defaults so the suite exercises the shipped behaviour. Individual
        # tests override what they need.
    },
}

REDIS = {
    'tasks': {
        'HOST': 'localhost',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 0,
        'SSL': False,
    },
    'caching': {
        'HOST': 'localhost',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 1,
        'SSL': False,
    },
}

# NetBox 4.7 raises InvalidMailer when something sends mail and no server is named. The
# plugin notifies in the interface rather than by mail, so nothing here sends any; this is
# only so a test which does cannot fail for a reason that has nothing to do with the plugin.
EMAIL = {
    'SERVER': 'localhost',
    'PORT': 25,
}

SECRET_KEY = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'

API_TOKEN_PEPPERS = {
    1: 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
}
