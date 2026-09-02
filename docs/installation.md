# Installation and configuration

| Component | Version |
|---|---|
| NetBox | `>= 4.7.0, < 4.8` |
| netbox-branching | `>= 1.2, < 1.3` |
| Python | `>= 3.12` |

This is the NetBox 4.7 line. For NetBox 4.6, stay on the 0.4.x line; the two do not overlap, because netbox-branching 1.1.x and 1.2 do not either. [The compatibility matrix](compatibility.md) has every release.

## Upgrading from the 4.6 line

There is no version of NetBox which 0.4.x and 0.5.x share, so this is a move between lines rather than an ordinary upgrade. NetBox 4.7 replaced django-mptt with a PostgreSQL `ltree` implementation and dropped the `lft`, `rght`, `tree_id` and `level` columns. netbox-branching 1.1.x reads those columns and cannot run on 4.7; netbox-branching 1.2 rewrote that part and requires 4.7. The plugin sits on top of whichever is installed and inherits the split.

Upgrade NetBox and netbox-branching together, then the plugin. Follow NetBox's own upgrade guide for the rest; these are only the points this plugin adds to it.

1. **Check the platform.** NetBox 4.7 requires PostgreSQL 15 or later and Redis 6.0 or later. It installs the PostgreSQL `ltree` extension on the first migration, which needs the `CREATE` privilege on the database. A NetBox user which owns its database already holds it.
2. **Drain the queues before restarting the workers.** NetBox 4.7 removed an argument from the function a queued webhook job calls, so a job still enqueued across the restart fails.
3. **Plan a window.** The 4.7 migration backfills every hierarchical table, then rebuilds the config context cache for every device and virtual machine. Both scale with the size of the database, and neither is reversible in practice.
4. **Merge or delete open branches first, if you can.** A branch schema is a copy of the main schema as it stood when the branch was created, and carrying one across a migration of this size is the least tested path there is.
5. **Then run `manage.py migrate netbox_change_control`.** The one migration this release adds records a field change NetBox made to a base class. It writes no data and takes no time.

Nothing in the plugin's own data changes. Change requests, policies, rules, reviews, comments and checks all survive untouched.

Two things behave differently afterwards, both covered in the [changelog](changelog.md): a webhook payload no longer carries the top-level `username` and `request_id` keys, and [policy conditions](policy-conditions.md) can read the transition itself.

## Installation

Install the package into the NetBox virtual environment, then enable it **before** `netbox_branching`, which must stay last in the list:

```python
# configuration/plugins.py
PLUGINS = [
    'netbox_change_control',
    'netbox_branching',
]

PLUGINS_CONFIG = {
    'netbox_branching': {
        # Required. See the warning below.
        'exempt_models': ['netbox_change_control.*'],
    },
    'netbox_change_control': {},
}
```

Then run the migrations:

```bash
./manage.py migrate netbox_change_control
```

If you are adding this plugin to a NetBox that already holds data, build the search index once so existing objects are findable:

```bash
./manage.py reindex netbox_change_control
```

!!! info "Important"

    The `exempt_models` entry is not optional. Without it, a change request created while a branch is active is written into that branch's schema and is invisible from main, taking the record of who approved what with it.

## Configuration

Every setting is optional. The defaults are safe to install on an existing NetBox: nothing is blocked that was not blocked before, except that a branch now needs an approved change request to merge.

| Setting | Default | Meaning |
|---|---|---|
| `protect_main` | `False` | Block writes to branching-supported models outside a branch. |
| `protect_main_scope` | `[]` | Limit `protect_main` to specific models. Empty protects every branching-supported model. |
| `enforce_merge_gate` | `True` | Refuse to merge a branch without an approved change request. |
| `notify_reviewers` | `True` | Raise NetBox notifications on status transitions. |
| `branch_page_placement` | `['right_page']` | Where a branch page shows its change request. `right_page` puts it in a card in the right-hand column, `alerts` puts it across the top of the page, both shows both, and `[]` shows neither. |
| `enable_builtin_checks` | `True` | Which built-in checks are available to policies. `True` for all, `False` for none, or a list of names. A check applies only where a policy names it. |
| `required_external_checks` | `[]` | Checks reported through the REST API. Each blocks the merge until reported. |
| `enable_auto_merge` | `True` | Allow requests that opt in to merge themselves once every gate passes. When `False`, the periodic job is not registered at all. |
| `auto_merge_interval` | `10` | Minutes between automatic merge sweeps. Keep every change window longer than this. Takes effect when the worker restarts. |
