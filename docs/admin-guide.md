# Administration guide

How to set this plugin up for a team: who gets which permissions, how to build the policies that govern your changes, and how to check the whole thing works before you rely on it.

If you only want the list of permission names, [Permissions](permissions.md) is the short reference. This page is the guide.

## Prerequisites

Before granting anything:

1. [netbox-branching](https://github.com/netboxlabs/netbox-branching) is installed and working, and your team already knows how to create a branch and make changes inside it.
2. This plugin is installed, `exempt_models` is set, and the migrations have run. See [Installation and configuration](installation.md).
3. You are comfortable with NetBox's own permission system. This plugin adds no permission machinery of its own; it uses NetBox object permissions exactly as every other model does.

## Permissions

### How permissions work here

This plugin does not invent a permission model. It declares the standard `view`, `add`, `change` and `delete` actions on each of its models, plus four custom actions, and NetBox enforces them.

That has one consequence worth stating plainly, because it decides how you should grant them:

> [!IMPORTANT]
> A NetBox object permission applies to **every object of that type**. Granting `netbox_change_control.delete_review` lets that user delete **any** review, not only their own. This is standard NetBox behaviour, not a quirk of this plugin. If you want a narrower grant, add a **constraint** to the permission. See [narrowing a permission with a constraint](#narrowing-a-permission-with-a-constraint).

Deleting somebody's review changes the outcome of the gate: removing a **Request changes** review removes the rejection, and the request moves off Rejected. So `delete_review` is a privileged grant. Treat it the way you would treat the ability to close a ticket on someone else's behalf.

> [!NOTE]
> Editing a review is different, and deliberately so. A reviewer may edit only their own review whatever permissions they hold; a superuser may edit any. A review is one person's statement about one change, so reassigning or rewriting somebody else's would forge their position. Only the delete action follows the plain NetBox model.

### Permission matrix

Three roles cover most deployments. Build them as NetBox groups and assign object permissions to the group rather than to individual users.

- **Contributor** opens branches and change requests, and comments on changes. Cannot approve.
- **Reviewer** everything a contributor can do, plus submitting reviews.
- **Administrator** manages policies and checks, and holds the two exemptions.

#### Branch permissions

These belong to netbox-branching, not to this plugin, but a change request is worthless without them.

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_branching.view_branch` | yes | yes | yes |
| `netbox_branching.add_branch` | yes | yes | yes |
| `netbox_branching.change_branch` | yes | yes | yes |
| `netbox_branching.sync_branch` | yes | yes | yes |
| `netbox_branching.merge_branch` | no | no | yes |
| `netbox_branching.view_changediff` | yes | yes | yes |

> [!NOTE]
> NetBox reads a permission name as `<app_label>.<action>_<model>`, splitting on the **last** underscore. So branching's `merge` action on the `Branch` model is written `netbox_branching.merge_branch`, and its `sync` action is `netbox_branching.sync_branch`. The action you tick in the permission form is `merge`, not `merge_branch`.

`view_changediff` is what gates the **Changes** tab. Without it a reviewer cannot see what they are being asked to approve.

#### Change request permissions

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_change_control.view_changerequest` | yes | yes | yes |
| `netbox_change_control.add_changerequest` | yes | yes | yes |
| `netbox_change_control.change_changerequest` | yes | yes | yes |
| `netbox_change_control.delete_changerequest` | no | no | yes |
| `netbox_change_control.override_window_changerequest` | no | no | optional |
| `netbox_change_control.abandon_changerequest` | no | no | yes |
| `netbox_change_control.reopen_changerequest` | no | no | yes |

Deleting a change request destroys the record of who approved what. Keep it with the administrators.

`status` is not editable, by anybody. It is derived from the policy evaluation, so it is read-only on the REST API and absent from the bulk edit form. **Abandon** and **Reopen** are the two transitions a person makes by hand, and each has its own permission and its own button on the change request page. Grant them to whoever is allowed to call off a change. Contributors do not need them to give up on their own work; they can delete a draft they own, or ask an administrator.

#### Review permissions

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_change_control.view_review` | yes | yes | yes |
| `netbox_change_control.add_review` | no | yes | yes |
| `netbox_change_control.change_review` | no | yes | yes |
| `netbox_change_control.delete_review` | no | no | yes |

`add_review` is the one that separates a reviewer from a contributor. A user without it sees the reason in place of the review form rather than a form that fails on submit.

`change_review` only ever lets a person edit their **own** review, whoever holds it.

`delete_review` is model-wide. See the warning above, and consider a constraint.

#### Comment permissions

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_change_control.view_changecomment` | yes | yes | yes |
| `netbox_change_control.add_changecomment` | yes | yes | yes |
| `netbox_change_control.change_changecomment` | yes | yes | yes |
| `netbox_change_control.delete_changecomment` | no | no | yes |

`change_changecomment` is what allows resolving and reopening a thread. If you use the `threads-resolved` check, everybody who might need to unblock a merge needs it.

#### Check permissions

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_change_control.view_mergecheck` | yes | yes | yes |
| `netbox_change_control.add_mergecheck` | no | no | yes |
| `netbox_change_control.change_mergecheck` | no | optional | yes |
| `netbox_change_control.delete_mergecheck` | no | no | yes |

`change_mergecheck` does two things: it shows the **Re-run checks** button, and it is what a CI token needs to report a result over the REST API. Give the token its own user and its own permission, constrained if you can, rather than reusing a person's.

> [!TIP]
> A reporting token cannot weaken the gate even with this permission. `required` is read-only on the API, and the gate reads requiredness from the configuration and the policies rather than from the stored row.

#### Policy permissions

| Permission | Contributor | Reviewer | Administrator |
|---|:---:|:---:|:---:|
| `netbox_change_control.view_policy` | yes | yes | yes |
| `netbox_change_control.view_policyrule` | yes | yes | yes |
| `netbox_change_control.add_policy` | no | no | yes |
| `netbox_change_control.change_policy` | no | no | yes |
| `netbox_change_control.delete_policy` | no | no | yes |
| `netbox_change_control.add_policyrule` | no | no | yes |
| `netbox_change_control.change_policyrule` | no | no | yes |
| `netbox_change_control.delete_policyrule` | no | no | yes |
| `netbox_change_control.bypass_policy` | no | no | optional |

Give everybody `view_policy` and `view_policyrule`. A reviewer who cannot read the policy cannot tell why they were asked, and the approval panel names rules the reader may not be able to open.

Editing a policy changes who must approve every open change request bound to it. That is the whole gate, so keep it with the administrators.

#### The two exemptions

Both are custom actions, and both are optional.

| Permission | Grants | Grant it to |
|---|---|---|
| `netbox_change_control.bypass_policy` | Write outside a branch while `protect_main` is enabled. | Automation accounts, and the people who run an incident. |
| `netbox_change_control.override_window_changerequest` | Merge a change request outside its change window. | Whoever is allowed to break a change freeze. |

To grant them, go to **Administration > Permissions > Add**, and enter the action in the *additional actions* field rather than ticking view, add, change or delete:

| Exemption | Object type | Action to enter |
|---|---|---|
| Write directly to main | Change Control > Policy | `bypass` |
| Merge outside the change window | Change Control > Change Request | `override_window` |

> [!IMPORTANT]
> The trailing part of a custom permission name has to be a real model name, because NetBox splits on the last underscore. That is why the bypass lives on `Policy` and the window override on `ChangeRequest`, rather than on names that would read better.

> [!NOTE]
> Superusers hold every permission, so they are exempt from `protect_main` and from every change window without being granted anything.

### Setting up groups

1. Go to **Administration > Groups** and create `Change Contributors`, `Change Reviewers` and `Change Administrators`.
2. Go to **Administration > Permissions > Add**. Give the permission a name, tick the actions, choose the object types, and assign it to a group.
3. Group one permission per area rather than making one giant permission. `Change control: reviews`, `Change control: policies` and so on are far easier to audit later.
4. Add users to groups. Do not assign object permissions directly to users except for service accounts.

The reviewer groups you name in a **policy rule** are the same NetBox groups. A user must be in the group **and** hold `add_review` for their approval to count: the group decides eligibility, the permission decides whether they can act at all.

### Narrowing a permission with a constraint

An object permission can carry a constraint, which is a JSON query applied to every object it covers. This is how you grant an action on your own objects only.

To let reviewers delete their own reviews and nobody else's, create a permission with the `delete` action on **Change Control > Review** and this constraint:

```json
{"reviewer": "$user"}
```

`$user` resolves to the signed-in user. The same pattern works elsewhere:

| Goal | Object type | Constraint |
|---|---|---|
| Delete only my own reviews | Review | `{"reviewer": "$user"}` |
| Delete only my own comments | Change Comment | `{"author": "$user"}` |
| Manage only my own change requests | Change Request | `{"requester": "$user"}` |
| Report results for one check only | Merge Check | `{"name": "ci-pipeline"}` |

> [!TIP]
> The last one is the right shape for a CI token. It can report the check it owns and touch nothing else.

## Configuring policies

### Creating a policy

**Change Control > Policies > Add**.

| Field | What to put in it |
|---|---|
| Name | What it governs, in your team's language. `Circuit changes`, not `Policy 3`. |
| Enabled | Leave ticked. A disabled policy is never attached to anything. |
| Weight | Display and evaluation order. Lower is listed first. Leave at 1000 unless you care. |
| Object types | The types this policy governs. **Leave empty to match every branch**, which is how you build a baseline. |
| Conditions | Optional. Narrows on the values of the changed objects. See [Policy conditions](policy-conditions.md). |
| Condition state | Which side of the change the conditions read. The default reads both. |
| Registered checks | Opt-in checks to require wherever this policy applies. |
| Reported checks | Names your own systems report over the REST API. |

Policies attach on their own, from the object types the branch touches, and they keep following the branch as it changes. The author cannot pick them and cannot remove them. See [policies attach automatically](policies.md#policies-attach-automatically).

### Configuring policy rules

A policy with no rules asks for nothing, so add at least one. **Change Control > Policy Rules > Add**.

| Field | What to put in it |
|---|---|
| Policy | The policy this rule belongs to. |
| Name | What the requirement is. `Two engineers`, `One lead`. This name is shown to reviewers. |
| Minimum reviews | How many eligible people must approve. Zero means none; the checks become the only gate. |
| Reviewer groups | Members of any listed group may satisfy it. |
| Reviewers | Individually named users may satisfy it. |

A user satisfies a rule if they are in **any** of its groups **or** are named on it. A policy is satisfied only when **every** one of its rules is.

An approval counts only toward the rules the approver is eligible for. A lead approving does not advance a rule that asks for engineers, which is the property a plain approval counter gets wrong.

### The pre-merge gate

Once the plugin is installed, a branch cannot merge unless it carries an approved change request whose policies are still satisfied, every required check passes, and the change window is open. The button is hidden rather than failing after the click, because the gate is registered as a netbox-branching pre-action validator.

Set `enforce_merge_gate` to `False` only to troubleshoot. It turns off the reason the plugin exists.

## Policy configuration examples

**Single approval, everywhere.** The baseline every deployment should start with.

| Field | Value |
|---|---|
| Object types | *(empty)* |
| Registered checks | `has-changes`, `no-conflicts`, `not-stale` |
| Rule | `One engineer`, minimum reviews 1, group `Change Engineers` |

**Two-person rule.** For anything that can take a site off the air.

| Field | Value |
|---|---|
| Object types | `dcim.device`, `dcim.interface` |
| Rule | `Two engineers`, minimum reviews 2, group `Change Engineers` |

**Senior approval on top.** Two rules on one policy, so both must be met.

| Field | Value |
|---|---|
| Object types | `circuits.circuit`, `circuits.circuittermination` |
| Rule 1 | `One engineer`, minimum reviews 1, group `Change Engineers` |
| Rule 2 | `One lead`, minimum reviews 1, group `Change Leads` |

**Live objects only.** The same policy, narrowed so a planned circuit stays a one-person change.

| Field | Value |
|---|---|
| Object types | `circuits.circuit` |
| Conditions | `{"attr": "status", "value": "active"}` |
| Condition state | Either side of the change |

**No human at all.** For scripted, low-risk work that must still pass the machine gate.

| Field | Value |
|---|---|
| Object types | `ipam.prefix`, `ipam.ipaddress` |
| Registered checks | `has-changes`, `no-conflicts`, `not-stale` |
| Rule | `No approval required`, minimum reviews 0 |

> [!WARNING]
> A zero rule removes the human requirement **of its own policy only**. If your baseline policy also matches the branch and asks for an engineer, the change still waits for that engineer. Scope the automatic policy so it is the only one matching, or narrow the baseline with conditions.

## Testing the workflow

Do this once, on a test instance, before you rely on any of it.

1. Create the three groups and their permissions.
2. Create the baseline policy with one rule asking for one engineer.
3. Create a second policy scoped to `dcim.device`, asking for one lead.
4. Sign in as a contributor. Create a branch and change a prefix inside it.
5. Open a change request against that branch and submit it for review.
6. Confirm the **Applied policies** card lists the baseline only, and the checks have run.
7. Sign in as a reviewer and approve. Confirm the status becomes **Approved** and the merge button appears for whoever holds `merge_branch`.
8. Back as the contributor, edit a **device** inside the same branch.
9. Confirm the request drops back to **Needs review**, and that the device policy has now attached and is asking for a lead. This is the check that the gate follows the branch rather than freezing at submission.
10. Approve as a lead, then merge. Confirm the request becomes **Completed**.

Steps 8 and 9 are the ones worth repeating after any upgrade.

## Troubleshooting

**The merge button is greyed out and the reason says the request is not approved.** The people gate and the machine gate are separate. Read the **Approval status** card: a rule showing 0/1 names who may satisfy it.

**A reviewer's approval did not count.** Three usual causes. They are not in a group the rule names. They approved, then the branch changed and their review went stale, which is shown with a **Stale** badge. Or they approved a different rule's requirement and the one still short asks for somebody else.

**The approval panel says no policy rules apply.** No enabled policy matched the branch, or every matching policy has no rules. A request with no rules is never satisfied, on purpose: an unpoliced merge is what the plugin exists to prevent. Add a baseline policy with no object types.

**Checks sit on pending forever.** A name that is not a registered check is treated as reported from outside, and waits for something to report it. Either something must PATCH a result, or the name is a typo in the policy's **Reported checks** field.

**A conflict is reported that you believe you already resolved.** netbox-branching never advances a diff's baseline, so a field main touched before your last sync stays flagged. This plugin distinguishes the two and says so on the request. See [Conflicts with main](conflicts.md).

**A user cannot see the Changes tab.** They are missing `netbox_branching.view_changediff`.

**`protect_main` is blocking a script.** Writes with no request context are allowed, so a management command or a background job is not affected. An interactive write needs `netbox_change_control.bypass_policy`.

**A change with a window never merges automatically.** The sweep runs every `auto_merge_interval` minutes and can step over a shorter window. The request warns about this itself. See [the sweep interval](merging.md#the-sweep-interval).
