from django.utils.translation import gettext_lazy as _
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label=_('Change Control'),
    groups=(
        (
            _('Requests'),
            (
                PluginMenuItem(
                    link='plugins:netbox_change_control:changerequest_list',
                    link_text=_('Change Requests'),
                    auth_required=True,
                    permissions=['netbox_change_control.view_changerequest'],
                    buttons=(
                        PluginMenuButton(
                            'plugins:netbox_change_control:changerequest_add',
                            _('Add'),
                            'mdi mdi-plus-thick',
                            permissions=['netbox_change_control.add_changerequest'],
                        ),
                    ),
                ),
                PluginMenuItem(
                    link='plugins:netbox_change_control:mergecheck_list',
                    link_text=_('Merge Checks'),
                    auth_required=True,
                    permissions=['netbox_change_control.view_mergecheck'],
                ),
            ),
        ),
        (
            _('Policies'),
            (
                PluginMenuItem(
                    link='plugins:netbox_change_control:policy_list',
                    link_text=_('Policies'),
                    auth_required=True,
                    permissions=['netbox_change_control.view_policy'],
                    buttons=(
                        PluginMenuButton(
                            'plugins:netbox_change_control:policy_add',
                            _('Add'),
                            'mdi mdi-plus-thick',
                            permissions=['netbox_change_control.add_policy'],
                        ),
                    ),
                ),
                PluginMenuItem(
                    link='plugins:netbox_change_control:policyrule_list',
                    link_text=_('Policy Rules'),
                    auth_required=True,
                    permissions=['netbox_change_control.view_policyrule'],
                    buttons=(
                        PluginMenuButton(
                            'plugins:netbox_change_control:policyrule_add',
                            _('Add'),
                            'mdi mdi-plus-thick',
                            permissions=['netbox_change_control.add_policyrule'],
                        ),
                    ),
                ),
            ),
        ),
    ),
    icon_class='mdi mdi-clipboard-check-outline',
)
