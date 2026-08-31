# Permissions

This is the short reference. For roles, a full permission matrix per model, constraint examples and troubleshooting, read the [Administration guide](admin-guide.md).

The plugin defines the standard NetBox object permissions for each of its models, plus one extra.

> [!IMPORTANT]
> A NetBox object permission applies to **every object of that type** unless you add a constraint. `delete_review` therefore lets a user delete anybody's review, which changes the outcome of the gate: removing a **Request changes** review removes the rejection. Grant it sparingly, or constrain it with `{"reviewer": "$user"}`. See [narrowing a permission with a constraint](admin-guide.md#narrowing-a-permission-with-a-constraint).
>
> Editing is the one exception. A reviewer may edit only their own review whatever they hold, because a review is one person's statement and rewriting somebody else's would forge their position.

| Permission | Grants |
|---|---|
| `netbox_change_control.view_changerequest` and friends | The usual view, add, change and delete on each model. |
| `netbox_change_control.change_changerequest` | Submit a change request for review, and edit one. |
| `netbox_change_control.add_review` | Submit a review. |
| `netbox_change_control.change_mergecheck` | Re-run checks. |
| `netbox_change_control.add_changecomment` | Comment on a specific change. |
| `netbox_change_control.change_changecomment` | Resolve and reopen threads. |
| `netbox_change_control.bypass_policy` | Write outside a branch while `protect_main` is enabled. |
| `netbox_change_control.override_window_changerequest` | Merge a change request outside its change window. |
| `netbox_change_control.abandon_changerequest` | Give up on an open change request. |
| `netbox_change_control.reopen_changerequest` | Take an abandoned change request back up. |

## Granting the exemptions

The two exemptions are **custom actions on an object type**. Grant them under **Administration > Permissions**:

| Exemption | Object type | Action to enter |
|---|---|---|
| Write directly to main | Change Control > Policy | `bypass` |
| Merge outside the change window | Change Control > Change Request | `override_window` |

Enter the action in the permission's *additional actions* field, not the view/add/change/delete checkboxes.

> [!NOTE]
> Superusers hold every permission, so they are always exempt from both.

> [!IMPORTANT]
> NetBox resolves a permission name as `<app_label>.<action>_<model>`, splitting on the **last** underscore. A custom permission whose trailing component is not a real model name can never be granted by an object permission, and the exemption silently ends up superuser-only. This is why the bypass lives on `Policy` and the window override on `ChangeRequest`.

A reviewer typically needs view and add on change requests, reviews and comments, plus view on policies, merge checks and branches.
