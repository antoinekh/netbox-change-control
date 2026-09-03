from netbox.plugins import PluginConfig, get_plugin_config

__version__ = '0.5.0'


class ChangeControlConfig(PluginConfig):
    name = 'netbox_change_control'
    verbose_name = 'NetBox Change Control'
    description = 'Policy-driven change control and mandatory review for NetBox branches'
    version = __version__
    base_url = 'change-control'
    # NetBox refuses to load the plugin outside this range, which is the only place the
    # requirement is enforced rather than documented. This release is the NetBox 4.7 line and
    # supports nothing older: 4.7 drops the django-mptt columns, so netbox-branching 1.1.x
    # cannot run on it, and 1.2 cannot run on 4.6. Stay on the 0.4.x line for NetBox 4.6.
    min_version = '4.7.0'
    max_version = '4.7.99'
    default_settings = {  # noqa: RUF012
        # Block writes to branching-supported models outside of a branch. Users holding the
        # netbox_change_control.bypass_policy permission are exempt.
        'protect_main': False,
        # Limit protect_main to specific models. Empty means every branching-supported model.
        # Entries are 'app_label.modelname' or 'app_label.*', for example ['circuits.*'].
        'protect_main_scope': [],
        # Refuse to merge a branch which has no approved change request. This is the core
        # guarantee of the plugin; disable it only to troubleshoot.
        'enforce_merge_gate': True,
        # Send a NetBox notification to the outstanding reviewers when a change request
        # needs review, and to the requester when it is approved or rejected.
        'notify_reviewers': True,
        # Where a branch page shows its change request. 'right_page' puts it in a card in
        # the right-hand column, beside branching's own cards; 'alerts' puts it in a band
        # across the top of the page, above them. Naming both shows both, and an empty list
        # shows neither.
        'branch_page_placement': ['right_page'],
        # Which built-in pre-merge checks to make available. True offers all of them, False
        # none, and a list a subset. Registering only makes a check selectable: a policy
        # decides where it applies, by naming it in its Checks field. Valid names:
        #   'has-changes'       the branch contains something to merge
        #   'no-conflicts'      no conflicts with main
        #   'not-stale'         the branch is recent enough to sync
        #   'threads-resolved'  every comment thread on the Changes tab is resolved
        'enable_builtin_checks': True,
        # Checks reported by an external system through the REST API. Each is created as
        # pending and blocks the merge until a result is reported. Entries are a name, or a
        # (name, label) pair.
        'required_external_checks': [],
        # Allow change requests to merge themselves once every gate is satisfied. Each
        # request must also opt in individually. Turning this off disables the feature
        # globally without editing any request, and stops the periodic job being registered.
        'enable_auto_merge': True,
        # Minutes between automatic merge sweeps. This bounds how late a change window can
        # fire: at 10, a window opening at 21:00 merges by 21:09. Lower it for tighter
        # windows, at the cost of one Job record per run. A change window shorter than this
        # interval may never merge automatically, and says so on the change request. Takes
        # effect when the worker restarts.
        'auto_merge_interval': 10,
    }

    def ready(self):
        super().ready()

        from netbox_branching.models import Branch

        # NetBox auto-imports a plugin's navigation, search and template_content modules, but
        # not its dashboard module, so a widget is registered only if something imports it.
        # Without this the My Reviews widget is missing from "Add widget", and a dashboard
        # already holding one fails to load at all.
        from . import dashboard, events, signal_receivers  # noqa: F401

        # Registering the job is what makes rqworker schedule it, so skipping the import
        # when auto-merge is off means no periodic job and no Job records at all.
        if get_plugin_config('netbox_change_control', 'enable_auto_merge'):
            from . import jobs  # noqa: F401
        from .checks import register_builtin_checks
        from .validators import require_approved_change_request

        Branch.register_preaction_check(require_approved_change_request, 'merge')

        # Registering makes a check available; a policy decides where it applies. True makes
        # every built-in available, False none, and a list a subset.
        selection = get_plugin_config('netbox_change_control', 'enable_builtin_checks')
        if selection:
            register_builtin_checks(list(selection) if isinstance(selection, (list, tuple, set)) else None)


config = ChangeControlConfig
