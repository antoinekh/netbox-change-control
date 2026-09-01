# Permissions

Every permission this plugin defines, and what each one grants. Nothing here is abbreviated: if a permission is not on this page, the plugin does not define it.

For roles, groups and how to assemble them, read the [Administration guide](admin-guide.md).

> [!IMPORTANT]
> A NetBox object permission applies to **every object of that type** unless you add a constraint. `delete_review` therefore lets a user delete anybody's review, which changes the outcome of the gate: removing a **Request changes** review removes the rejection. Grant it sparingly, or constrain it with `{"reviewer": "$user"}`. See [narrowing a permission with a constraint](admin-guide.md#narrowing-a-permission-with-a-constraint).

> [!NOTE]
> NetBox reads a permission name as `<app_label>.<action>_<model>`, splitting on the **last** underscore. The **Action** column below is what you tick, or type into *additional actions*, on the permission form. The full name is what code and the REST API use.

## Change requests

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_changerequest` | `view` | See change requests, with their reviews, checks and applied policies. |
| `netbox_change_control.add_changerequest` | `add` | Open a change request against a branch. |
| `netbox_change_control.change_changerequest` | `change` | Edit the title, description, reference, priority, change window and auto-merge flag, and move the request between **Draft** and **Needs review** with **Submit for review** and **Return to draft**. |
| `netbox_change_control.delete_changerequest` | `delete` | Delete a change request, destroying the record of who approved what. |
| `netbox_change_control.abandon_changerequest` | `abandon` | Give up on an open change request, through the button or `POST /change-requests/{id}/abandon/`. |
| `netbox_change_control.reopen_changerequest` | `reopen` | Take an abandoned change request back up, through the button or `POST /change-requests/{id}/reopen/`. |
| `netbox_change_control.override_window_changerequest` | `override_window` | Merge a change request outside its change window. |

`status` is not editable by anybody. It is read-only on the REST API and absent from the bulk edit form. Four actions move it, and [the lifecycle diagram](change-requests.md#the-lifecycle) shows how they fit together:

| Action | Permission |
|---|---|
| Submit for review | `change_changerequest` |
| Return to draft | `change_changerequest` |
| Abandon | `abandon_changerequest` |
| Reopen | `reopen_changerequest` |

Everything else is derived: **Needs review**, **Approved** and **Rejected** follow from the reviews, and nobody sets them.

## Reviews

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_review` | `view` | See who has reviewed a change request and what they said. |
| `netbox_change_control.add_review` | `add` | Submit a review: approve, request changes, or comment. This is what separates a reviewer from everybody else. |
| `netbox_change_control.change_review` | `change` | Edit a review. Only ever your **own**, whoever holds this. A superuser may edit any. |
| `netbox_change_control.delete_review` | `delete` | Delete **any** review, unless constrained. See the warning above. |

Holding `add_review` is not enough on its own to advance a policy rule. The rule names groups and users, and only somebody it names can satisfy it. The permission decides whether you may act; the rule decides whether your approval counts.

## Change comments

The per-object discussion on the **Changes** tab.

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_changecomment` | `view` | Read the comment threads on a change request. |
| `netbox_change_control.add_changecomment` | `add` | Comment on one changed object, and reply within a thread. |
| `netbox_change_control.change_changecomment` | `change` | Resolve and reopen a thread. Anybody who might have to clear the `threads-resolved` check needs this. |
| `netbox_change_control.delete_changecomment` | `delete` | Delete a comment. Also available from the Changes tab. |

## Merge checks

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_mergecheck` | `view` | See check results on a change request and in the Merge Checks list. |
| `netbox_change_control.add_mergecheck` | `add` | Create a check row by hand. The plugin creates them from the policies, so this is rarely wanted. |
| `netbox_change_control.change_mergecheck` | `change` | Press **Re-run checks**, and report a result over the REST API. This is what a CI token needs. |
| `netbox_change_control.delete_mergecheck` | `delete` | Delete a check row. Deleting one does not open the gate: a required check with no result counts as not run, and blocks. |

> [!TIP]
> A reporting token cannot weaken the gate even with `change_mergecheck`. `required` is read-only on the REST API, and the gate reads requiredness from the configuration and the policies rather than from the stored row.

## Policies

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_policy` | `view` | Read a policy: its scope, conditions and required checks. Give this to everybody, or a reviewer cannot tell why they were asked. |
| `netbox_change_control.add_policy` | `add` | Create a policy. |
| `netbox_change_control.change_policy` | `change` | Edit a policy, which changes who must approve every open change request bound to it. |
| `netbox_change_control.delete_policy` | `delete` | Delete a policy. Refused while a change request still references it. |
| `netbox_change_control.bypass_policy` | `bypass` | Write outside a branch while `protect_main` is enabled. |

## Policy rules

| Permission | Action | Grants |
|---|---|---|
| `netbox_change_control.view_policyrule` | `view` | Read a rule: how many approvals it needs, and who may give them. |
| `netbox_change_control.add_policyrule` | `add` | Add a rule to a policy. |
| `netbox_change_control.change_policyrule` | `change` | Edit a rule, including its minimum and its reviewer groups. |
| `netbox_change_control.delete_policyrule` | `delete` | Remove a rule from a policy. |

## Policy bindings

The table recording which policies govern which change request **defines no permissions at all**. The plugin maintains it, no page exposes it, and the four Django creates for every model are switched off.

> [!NOTE]
> There is therefore nothing to grant in order to detach a policy from a change request. Which policies govern a change is decided from the objects its branch touches, and they are re-matched as the branch moves, so a binding removed by hand comes back. Change the policy's scope instead.

## Permissions from netbox-branching

These belong to [netbox-branching](https://github.com/netboxlabs/netbox-branching) rather than to this plugin, but a change request is useless without them, and the split between the two catches people out.

| Permission | Action | Grants |
|---|---|---|
| `netbox_branching.view_branch` | `view` | See branches. |
| `netbox_branching.add_branch` | `add` | Create a branch. |
| `netbox_branching.change_branch` | `change` | Rename a branch and edit its description. |
| `netbox_branching.delete_branch` | `delete` | Delete a branch. The change request survives it, keeping the branch name. |
| `netbox_branching.merge_branch` | `merge` | Merge a branch. This is what shows the **Merge branch** button once every gate is satisfied. |
| `netbox_branching.sync_branch` | `sync` | Pull main's changes into a branch. |
| `netbox_branching.revert_branch` | `revert` | Revert a merged branch. |
| `netbox_branching.archive_branch` | `archive` | Archive a merged branch. |
| `netbox_branching.migrate_branch` | `migrate` | Apply outstanding migrations to a branch. |
| `netbox_branching.view_changediff` | `view` | See the branch diff. **This is what gates the Changes tab**: without it, a reviewer cannot see what they are being asked to approve. |

## Granting the custom actions

Four actions are not the usual view, add, change and delete. Grant them under **Administration > Permissions > Add**, typing the action into the *additional actions* field rather than ticking a box:

| To allow | Object type | Action to enter |
|---|---|---|
| Writing directly to main under `protect_main` | Change Control > Policy | `bypass` |
| Merging outside the change window | Change Control > Change Request | `override_window` |
| Abandoning a change request | Change Control > Change Request | `abandon` |
| Reopening an abandoned change request | Change Control > Change Request | `reopen` |

> [!IMPORTANT]
> The trailing part of a custom permission name has to be a real model name, because NetBox splits on the last underscore. That is why the bypass lives on `Policy`: `bypass_change_control` would resolve to a model called `control`, which does not exist, so the permission could never be granted through an object permission at all and the exemption would silently be superuser-only.

> [!NOTE]
> Superusers hold every permission, so they are exempt from `protect_main` and from every change window without being granted anything.
