# Permissions

The plugin defines the standard NetBox object permissions for each of its models, plus one extra.

| Permission | Grants |
|---|---|
| `netbox_change_control.view_changerequest` and friends | The usual view, add, change and delete on each model. |
| `netbox_change_control.add_review` | Submit a review. |
| `netbox_change_control.change_mergecheck` | Re-run checks. |
| `netbox_change_control.add_changecomment` | Comment on a specific change. |
| `netbox_change_control.change_changecomment` | Resolve and reopen threads. |
| `netbox_change_control.bypass_policy` | Write outside a branch while `protect_main` is enabled. |
| `netbox_change_control.override_window_changerequest` | Merge a change request outside its change window. |

## Granting the exemptions

The two exemptions are **custom actions on an object type**, the same mechanism the commercial product uses for `netbox_changes.bypass_policy`. Grant them under **Administration > Permissions**:

| Exemption | Object type | Action to enter |
|---|---|---|
| Write directly to main | Change Control > Policy | `bypass` |
| Merge outside the change window | Change Control > Change Request | `override_window` |

Enter the action in the permission's *additional actions* field, not the view/add/change/delete checkboxes.

> [!NOTE]
> Superusers hold every permission, so they are always exempt from both. Test an exemption with an ordinary account.

> [!IMPORTANT]
> NetBox resolves a permission name as `<app_label>.<action>_<model>`, splitting on the **last** underscore. A custom permission whose trailing component is not a real model name can never be granted by an object permission, and the exemption silently ends up superuser-only. This is why the bypass lives on `Policy` and the window override on `ChangeRequest`.

A reviewer typically needs view and add on change requests, reviews and comments, plus view on policies, merge checks and branches.
