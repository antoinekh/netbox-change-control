# Installation and configuration

| Component | Version |
|---|---|
| NetBox | `>= 4.6.9, < 4.7` |
| netbox-branching | `>= 1.1.3, < 1.2` |
| Python | `>= 3.12` |

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

> [!IMPORTANT]
> The `exempt_models` entry is not optional. Without it, a change request created while a branch is active is written into that branch's schema and is invisible from main, taking the record of who approved what with it.

## Configuration

Every setting is optional. The defaults are safe to install on an existing NetBox: nothing is blocked that was not blocked before, except that a branch now needs an approved change request to merge.

| Setting | Default | Meaning |
|---|---|---|
| `protect_main` | `False` | Block writes to branching-supported models outside a branch. |
| `protect_main_scope` | `[]` | Limit `protect_main` to specific models. Empty protects every branching-supported model. |
| `enforce_merge_gate` | `True` | Refuse to merge a branch without an approved change request. |
| `lock_matched_policies` | `True` | Automatically matched policies cannot be detached by the author. |
| `notify_reviewers` | `True` | Raise NetBox notifications on status transitions. |
| `enable_builtin_checks` | `True` | Which built-in checks are available to policies. `True` for all, `False` for none, or a list of names. A check applies only where a policy names it. |
| `required_external_checks` | `[]` | Checks reported through the REST API. Each blocks the merge until reported. |
| `enable_auto_merge` | `True` | Allow requests that opt in to merge themselves once every gate passes. When `False`, the periodic job is not registered at all. |
| `auto_merge_interval` | `10` | Minutes between automatic merge sweeps. Keep every change window longer than this. Takes effect when the worker restarts. |
