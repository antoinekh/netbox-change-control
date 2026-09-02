# Event rules

A change request is an ordinary NetBox object, so NetBox's own [event rules](https://netboxlabs.com/docs/netbox/en/stable/features/event-rules/) can fire a webhook or run a script when one is created, updated, approved or rejected. That is how change control reaches the rest of your estate: a ticket system, a chat channel, a pipeline, or a model asked to read the diff.

## What this plugin emits

Six lifecycle event types are registered, and each is put through NetBox's event pipeline as it happens:

| Event type | Fires when |
|---|---|
| `change_request_submitted` | The author presses **Submit for review**. |
| `change_request_review_requested` | The request enters **Needs review**, including when a later edit invalidates an approval. |
| `review_submitted` | A reviewer records a decision, whether or not it moves the status. |
| `change_request_approved` | Every rule is satisfied and the status becomes **Approved**. |
| `change_request_rejected` | A reviewer requests changes and the status becomes **Rejected**. |
| `change_request_completed` | The branch has merged. |

The status ones fire on a **transition only**. `refresh_status` runs on many signals, so firing on every save would send a webhook per keystroke.

Two pairs are easy to confuse:

- `change_request_submitted` is the author's deliberate act. `change_request_review_requested` is the request arriving in Needs review, which also happens when an approved request is invalidated by a new commit on its branch. Watch the first for "somebody wants this reviewed", the second for "this needs attention again".
- `review_submitted` is one reviewer's decision. `change_request_approved` is the whole policy being satisfied. Four approvals against a rule needing five emit four of the first and none of the second.

A `review_submitted` event carries the **change request**, not the review, because that is the object an event rule is attached to. Read `data.status` to see where the request stands after the decision.

The ordinary object events (`object_created`, `object_updated`, `object_deleted`) fire as well, because a change request is a normal object. The two are independent: an approval produces both `object_updated` and `change_request_approved`, and a rule on either works.

!!! note

    The commercial NetBox Changes plugin registers similar lifecycle types but does not put them through the pipeline. Its [documentation](https://netboxlabs.com/docs/changes/event-rules/) says: "An event rule configured to trigger on one of these types will silently never fire. Use the standard object event types described below instead." Here both routes work, so a rule can name the transition it means.

## A rule on a lifecycle event

**Operations > Event Rules > Add**, then:

| Field | Value |
|---|---|
| Object types | `NetBox Change Control > Change Request` |
| Event types | `Change request approved` |
| Action type | Webhook |
| Webhook | your receiver |

Nothing else is required. The rule fires once per approval.

## A rule on an object event, with a condition

If you prefer to watch ordinary updates, match on the field you care about:

```json
{
  "attr": "status",
  "value": "approved"
}
```

This fires whenever a change request is saved while its status reads `approved`, which is not the same thing as the moment it became approved. A save that leaves the status alone still matches. Prefer the lifecycle event when you mean the transition.

## What the payload looks like

A webhook delivery is NetBox's standard shape. The `event` key carries the event type, and `data` is the serialised change request:

```json
{
  "event": "change_request_approved",
  "timestamp": "2026-08-26T18:44:49.406346+00:00",
  "object_type": "netbox_change_control.changerequest",
  "data": {
    "id": 39,
    "url": "/api/plugins/change-control/change-requests/39/",
    "display": "CR39: Upgrade the access switch firmware",
    "title": "Upgrade the access switch firmware",
    "status": {"value": "approved", "label": "Approved"},
    "priority": {"value": "medium", "label": "Medium"},
    "branch": {"id": 12, "name": "capture-ready", "url": "..."},
    "branch_name": "capture-ready",
    "branch_deleted": false,
    "requester": {"id": 1, "username": "admin", "url": "..."},
    "approved": true,
    "ready_to_merge": true,
    "merge_blocked_reason": "",
    "has_conflicts": false,
    "auto_merge": false,
    "scheduled_start": null,
    "scheduled_end": null,
    "description": "",
    "comments": "",
    "tags": [],
    "custom_fields": {},
    "created": "2026-08-26T18:38:50.495Z",
    "last_updated": "2026-08-26T18:44:49.401Z"
  },
  "snapshots": {
    "prechange": {"status": "needs-review", "...": "..."},
    "postchange": {"status": "approved", "...": "..."}
  },
  "request": {
    "id": "a37a4311-c2ec-48ed-affb-cbf942babefb",
    "path": "/plugins/change-control/change-requests/39/",
    "path_info": "/plugins/change-control/change-requests/39/",
    "method": "POST",
    "GET": {},
    "user": "admin"
  }
}
```

The fields worth acting on are `data.ready_to_merge`, `data.merge_blocked_reason`, `data.has_conflicts` and `data.branch_name`. `ready_to_merge` is the honest answer: approved is the people gate only, and a request can be approved while a check or the change window still blocks it.

`snapshots.prechange` is `null` on a create. On a lifecycle event it holds the state before the transition, so `snapshots.prechange.status` tells you where the request came from.

`request` is present only when the transition happened inside an HTTP request. A status change made by a background job, such as an approval invalidated by an automatic sweep, carries none of it, so read `request.user` and `request.id` defensively.

!!! warning "Changed in 0.5.0"

    NetBox 4.7 removed the top-level `username` and `request_id` keys from the webhook body. The same two values are still delivered, as `request.user` and `request.id`. A receiver written against the old names reads `null` from NetBox 4.7 onwards and must be updated.

## When nothing arrives

A webhook is fire-and-forget, so a failed delivery is only visible in the worker log:

```bash
docker compose logs netbox-worker
```

!!! info "Important"

    **Behind a corporate proxy, a webhook to an internal address fails.** NetBox resolves a proxy for every outgoing webhook through `PROXY_ROUTERS` and hands it to `requests.Session.send()`. requests only honours `no_proxy` when it builds the request itself, so setting `no_proxy` changes nothing here: the delivery goes to the proxy, which cannot route to a private address, and fails with a 502.

    `resolve_proxies` returns the first truthy result and a router cannot veto a later one, so the fix is a single router which declines to proxy private destinations and defers to the configured proxy for everything else:

    ```python
    PROXY_ROUTERS = ['my_config.PrivateBypassProxyRouter']
    ```

## Reporting a check back

An event rule is one half of a round trip. The other half is the [REST API for checks](custom-checks.md#checks-reported-by-an-external-system): the webhook tells your pipeline a change request is ready, the pipeline does its work, and it reports a pass or fail onto the change request, which gates the merge.

That is the shape to use for anything slow or external, including a model reviewing the diff. See [writing your own checks](custom-checks.md).

For anything NetBox can already answer on its own, there is no need to leave NetBox at all. Read on.

## Reporting a check without leaving NetBox

The plugin registers an action type of its own: **Change control: report a pre-merge check**. Pick it in the **Action type** dropdown on an event rule, and the rule writes a check result straight onto the change request, with no webhook, no pipeline and no code.

This is the difference between the two halves of a check:

| | Reported over the REST API | Reported by an event rule |
|---|---|---|
| Answers | Anything, including work done elsewhere | Anything NetBox already knows |
| Needs | A receiver, a token, and something to run it | An event rule |
| Written by | A developer | Whoever can add an event rule |
| Latency | However long the pipeline takes | Immediate |

Use the action for a rule you can state as a condition on the objects themselves. Use the REST API when something outside NetBox has to decide.

### The check has to exist first

The action reports a result for a check the change request already has. It never invents one, because a check the configuration does not expect is deleted at the next evaluation, and it would take its blocking result with it.

Declare the name first, in exactly the way any externally reported check is declared: name it in a policy's **Checks** field, or list it in `required_external_checks`. That is also what makes the check **required**, which is what lets a result block the merge.

If you skip this, the rule fires and does nothing. The worker log says which check it wanted and how to declare it.

### Configuring the action

The rule's **Action data** is a JSON object:

```json
{
  "check": "device-tenancy",
  "status": "failure",
  "summary": "active with no tenant assigned",
  "details_url": "https://wiki.example.com/change-policy"
}
```

| Key | Required | Meaning |
|---|---|---|
| `check` | Yes | The name of the check to report, as declared above |
| `status` | No | `pending`, `running`, `success`, `failure`, `error` or `skipped`. Defaults to `failure` |
| `summary` | No | The line shown beside the result. Defaults to naming the rule and the object that tripped it |
| `details_url` | No | A link shown with the result |

The form refuses a rule that names no check, or an unknown status, at the moment you save it. A rule that saves and then quietly does nothing would be far worse.

### A worked example

Refuse to merge a branch that puts a device into service without recording who it belongs to.

It takes **two** rules on one check, and that is the point of the example. One rule fails the check; the other is how it goes green again. A check that can only ever fail is a dead end, not a gate.

First, declare the check. Add `device-tenancy` to the **Checks** field of a policy scoped to `dcim.device`.

Then create two event rules, both on **Object types: `dcim.device`** and **Event types: Updated**, both using the **Change control: report a pre-merge check** action:

=== "Rule 1: the finding"

    | Field | Value |
    |---|---|
    | Name | Device active without a tenant |
    | Conditions | `{"and": [{"attr": "status.value", "value": "active"}, {"attr": "tenant", "value": null}]}` |
    | Action data | `{"check": "device-tenancy", "status": "failure", "summary": "active with no tenant assigned"}` |

=== "Rule 2: the all clear"

    | Field | Value |
    |---|---|
    | Name | Device active with a tenant |
    | Conditions | `{"and": [{"attr": "status.value", "value": "active"}, {"attr": "tenant", "value": null, "negate": true}]}` |
    | Action data | `{"check": "device-tenancy", "status": "success", "summary": "tenant assigned"}` |

Now, inside a branch:

| What somebody does | The check reads |
|---|---|
| Sets a device to **active**, no tenant | **Failed**, and the merge is refused |
| Assigns a tenant to that device | **Passed**, and the merge is allowed |
| Sets a device to **planned**, no tenant | Untouched. Neither rule matches, because the check is about devices going into service |

The two conditions are each other's negation, so exactly one of them ever matches. Write a pair that can both match and the result depends on which rule NetBox dispatched last.

!!! warning "`status.value`, not `status`"

    An event rule condition reads the **REST serialization** of the object, where a choice field is an object: `{"value": "active", "label": "Active"}`. So the path is `status.value`.

    A [policy condition](policy-conditions.md) reads the change diff, where the same field is the bare string `active`, and so does anything under `snapshots.`. The two are different shapes, and a condition written for one silently matches nothing in the other.

### Narrowing it to the moment it changes

The pair above fires on every update to an active device, not only the update that made it active. That is usually what you want for a data-quality gate, because it re-checks the answer on every edit.

When you do mean the transition, add the `changed` operator NetBox 4.7 introduced:

```json
{
  "and": [
    {"attr": "status", "op": "changed"},
    {"attr": "status.value", "value": "active"},
    {"attr": "tenant", "value": null}
  ]
}
```

Note the two shapes in one ruleset, which is correct and worth reading twice. `changed` resolves `status` inside each snapshot, where it is a bare string. `status.value` resolves against the payload, where it is an object.

Be careful what this costs you: gated on `changed`, rule 2 no longer fires when somebody later assigns the tenant, because that edit does not touch the status. The check stays red. Use the transition form for a rule that only ever reports, and the plain form for a pair that has to clear itself.

This is the same distinction a [policy condition](policy-conditions.md#reading-the-transition-itself) draws, and the two work well together: the policy decides who must approve, the check decides whether the merge is allowed at all.

### Which change request it lands on

You do not choose. Naming one on the rule would be useless, because a change request lasts days and a rule lasts for ever.

The action works it out from the event:

- A change made inside a branch belongs to that branch's change request.
- An event about a change request, from [the lifecycle events above](#what-this-plugin-emits), names one directly.
- A change made on main belongs to nothing, so the rule does nothing. This is normal, not a fault: the same rule fires on main and in every branch, and only the branch has something to gate.

!!! warning "Open the change request first"

    A rule can only report onto a change request that already exists. A change made in a branch before anybody opened one has nothing to report to, and is missed. Nothing re-runs it later.

    Open the change request as soon as you create the branch. The branch page offers a button for exactly that, and an empty branch is a normal state: the `has-changes` check is what stops it merging.

### When nothing happens

The action never raises. A rule it cannot satisfy is that rule's own problem, and failing loudly would abandon whatever else NetBox was dispatching alongside it.

It says what it did in the worker log instead:

```bash
docker compose logs netbox-worker | grep netbox_change_control
```

A repeated result is not written again. A rule that fires twice with the same answer leaves one entry in the changelog, not two, so the transitions worth reading are not buried.
