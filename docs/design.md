# Design

Status: approved and implemented. First written 2026-08-25, kept current as the plugin changes.

## Problem

NetBox Branching stages changes in a branch and merges them. It has no notion of who is allowed to approve a merge. NetBox Labs sells that capability as NetBox Changes, which is available to their clients only. The only open-source attempt, `netbox-branch-review`, hardcoded one or two approvals, had no reviewer groups, and its repository was deleted some time after August 2025. Its PyPI artifacts remain but are unmaintained.

## Goal

Give branch merges a policy-driven approval gate that an author cannot route around.

## Decisions

### Policies attach automatically, by scope

NetBox Changes lets the change request author pick which policies apply. That makes the gate advisory: an author wanting a fast merge picks the weakest policy.

Here, a `Policy` declares the object types it governs. When a change request is opened, the plugin reads `netbox_branching.ChangeDiff` for the branch, collects the object types actually touched, and attaches every matching enabled policy. Matched bindings are locked against the author.

An unscoped policy applies to every branch, which gives a baseline. An optional NetBox `ConditionSet` narrows a policy further, evaluated against each changed object; the policy matches if any one object satisfies it.

### The gate is a validator, not a signal

Branching offers both a `pre_merge` signal and pluggable pre-action validators. The signal only fires inside `Branch.merge()`, so blocking there produces an error after the user clicks merge.

`Branch.register_preaction_check(func, 'merge')` runs inside `Branch.can_merge`, which the UI reads to decide whether to render the merge button. A refusal therefore hides the button and carries a message. That is the better hook.

The validator re-evaluates the policies rather than trusting `ChangeRequest.status`. A stored "approved" whose reviews no longer satisfy the rules must not merge. This is covered by `test_approved_status_alone_does_not_open_the_gate`.

### Eligibility is per rule, not per request

A rule holds `min_reviews`, a set of groups and a set of users. A reviewer satisfies a rule when they are in one of its groups or named in it. An approval counts only toward the rules the reviewer is eligible for.

This is the property a naive approval counter gets wrong: a lead approving does not advance a rule that requires engineers. `test_lead_approval_does_not_count_toward_engineer_rule` pins it.

### Zero rules is not approval

A change request with no attached rules evaluates as unsatisfied. An unpoliced merge is exactly what the plugin exists to prevent, so an empty rule set must fail closed, not open.

### Plugin models are exempt from branching

Our models are change-logged, so branching would track them. A change request created while a branch was active was written into the branch schema and became invisible from main.

The fix is `exempt_models: ['netbox_change_control.*']` in the branching config. This is a required installation step, not a tuning knob.

### `protect_main` is off by default

Enabling it by default would break an existing install the moment the plugin is added. It is opt-in. It is implemented as `pre_save` and `pre_delete` receivers which refuse a write when `active_branch` is unset, the model supports branching, and the user lacks `netbox_change_control.bypass_policy`. Writes with no request context (migrations, scripts, jobs) are allowed, since they are not interactive edits.

## Data model

| Model | Base | Purpose |
|---|---|---|
| `Policy` | `PrimaryModel` | Name, enabled, weight, `object_types`, `conditions`. |
| `PolicyRule` | `NetBoxModel` | `min_reviews`, `groups`, `users`. Unique per policy name. |
| `ChangeRequest` | `PrimaryModel` | One per branch (`OneToOneField`). Status, priority, requester. |
| `ChangeRequestPolicy` | `Model` | Through table. Records `matched` and `matched_object_types`. |
| `Review` | `NetBoxModel` | One decision per reviewer per request, enforced by constraint. |
| `MergeCheck` | `NetBoxModel` | One pre-merge check result per request, unique on its name. |
| `ChangeComment` | `NetBoxModel` | A comment on one changed object, or a reply within that thread. |

`Review` was first a `ChangeLoggedModel`, which has no `tags`. `NetBoxModelFilterSet` filters on `tags__slug`, so it failed at import. Every public model is now `NetBoxModel` or `PrimaryModel`, which keeps the filtersets, serializers and tables uniform.

## Module layout

| Module | Responsibility |
|---|---|
| `policy.py` | Matching and evaluation. Pure functions over the database, no request needed, so they test in under a second. |
| `validators.py` | The merge gate. |
| `signal_receivers.py` | `protect_main`. |
| `models/` | Split into `policies.py` and `requests.py`. |

## Testing

Two layers.

Layer one is the test suite, one file per subject: the policy engine, the merge gate, the checks, change windows and automatic merging, conflicts, comment threads, `protect_main`, the filtersets, the REST API and the plugin hooks. Most of it constructs branches without provisioning a schema, which is what keeps a suite this size to about half a minute.

The shared fixtures live in `tests/base.py`, so the shape of a change request is declared once rather than in every file. Fixture classes carry no tests of their own: subclassing a class which has tests re-runs every one of them under the subclass, which inflates the count and covers nothing new.

Layer two is the seed command, `seed_change_control`. It creates the reviewer groups and users, the object permissions those users need, five graded policies, a branch with a real change, and an open change request. `--adopt` opens a change request for branches that already exist.

The seed commands live in a development-only plugin which is neither published nor committed, so a production install has no command that can invent policies, users or permissions, and a reader of this repository will not find one.

The seed's branch edit must run inside `netbox.context_managers.event_tracking` with a synthetic request. NetBox writes `ObjectChange` records only within a request context; without it the branch records no changes, `ChangeDiff` stays empty, and every scoped policy silently fails to match.

## Implementation notes

### Status is derived, so it is refreshed by signal

`ChangeRequest.status` caches the policy evaluation so it can be filtered and listed cheaply. Recomputing it only in the review-submission view left it stale whenever a review arrived by another path: the REST API, the object edit form, a bulk edit, a management command or the ORM. The page then showed "Satisfied" beside a status of "Needs review".

`post_save` and `post_delete` receivers on `Review` and `ChangeRequestPolicy` now call `refresh_status`, so no caller can forget. Terminal statuses are still never reopened. `tests/test_status.py` pins each path.

The merge gate does not depend on this cache; it re-evaluates. The cache is for display and filtering only.

### Repeated work is collapsed, not deferred

Refreshing a change request is correct on every event that could change its answer, and doing
it per event is what keeps the cached status honest without any caller having to remember.
The cost is that one user action is often many events: attaching policies is one signal each.

`netbox_change_control/batching.py` collapses those bursts. A caller that knows it is about to
cause one wraps it in `batched()`, and each affected change request is refreshed once when the
block ends.

It deliberately does not use `transaction.on_commit`. Deferring past the commit would mean a
request's checks are not yet run when the view that submitted it renders the next page, and it
would make every test that asserts on a check result depend on `captureOnCommitCallbacks`.
Outside a block the refresh is immediate, so nothing changes for the callers which are not
part of a burst.

### Staleness is derived from a snapshot, not a flag

A review stores `branch_change_time`, the timestamp of the newest change in its branch when the review was submitted. A review is stale when the branch's newest change is later than that snapshot.

This is preferable to a `stale` boolean maintained by signals, because a flag can drift while a derived value cannot. It also means approval invalidation needs no separate mechanism: stale approvals are simply excluded from the evaluation, the rules stop being satisfied, and `refresh_status` returns the request to Needs review.

### `protect_main` listens to every write

`protect_main_on_save` and `protect_main_on_delete` are registered without a sender, so they
run for every model write anywhere in NetBox. That is deliberate rather than an oversight: the
set of protected models is not knowable at import time, because it depends on which models
branching supports and on `protect_main_scope`, which is configuration.

The cost is bounded by ordering the guard cheaply. The first thing each receiver does is read
`protect_main`, and with it off, which is the default, that is the whole cost of the call.

### `protect_main_scope`

The commercial product's `protect_main` is all-or-nothing. In practice a team often wants branch discipline on one risky area, such as circuits, without forcing every IPAM edit through review.

`protect_main_scope` accepts `app_label.modelname` and `app_label.*` entries. An empty list keeps the original all-or-nothing behaviour, so the default is unchanged.

### Notifications reuse NetBox's inbox

NetBox already has a per-user notification model and a bell menu, so the plugin only decides who to tell and when. Notifications fire on a status transition, never on every save, because `refresh_status` runs on many signals and would otherwise spam reviewers.

Recipients are computed from the unsatisfied rules only, so a reviewer is not pestered about a rule their colleagues already met. The requester is always excluded from review requests, since they cannot review their own change; they are instead notified on approval and rejection, being the person who acts next.

`Notification` is unique per (object type, object id, user), so delivery uses `update_or_create` and clears `read`. A repeat transition re-raises the existing notification rather than failing on the constraint.
