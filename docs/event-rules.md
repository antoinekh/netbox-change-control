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
  "username": "admin",
  "request_id": "a37a4311-c2ec-48ed-affb-cbf942babefb",
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
  }
}
```

The fields worth acting on are `data.ready_to_merge`, `data.merge_blocked_reason`, `data.has_conflicts` and `data.branch_name`. `ready_to_merge` is the honest answer: approved is the people gate only, and a request can be approved while a check or the change window still blocks it.

`snapshots.prechange` is `null` on a create. On a lifecycle event it holds the state before the transition, so `snapshots.prechange.status` tells you where the request came from.

`username` and `request_id` are present only when the transition happened inside an HTTP request. A status change made by a background job, such as an approval invalidated by an automatic sweep, carries neither.

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
