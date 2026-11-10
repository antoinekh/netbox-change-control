"""
Signal receivers, one module per concern. Importing the package connects them all.
"""

from netbox_change_control.receivers import branches, checks, deleting, protect_main, status  # noqa: F401
