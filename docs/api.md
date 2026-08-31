# REST API

Every model has a full REST endpoint under `/api/plugins/change-control/`:

| Endpoint | Model |
|---|---|
| `policies/` | Policy |
| `policy-rules/` | PolicyRule |
| `change-requests/` | ChangeRequest |
| `reviews/` | Review |
| `checks/` | MergeCheck |
| `change-comments/` | ChangeComment |

They behave like any NetBox endpoint: token authentication, the same filters as the list views, and the same object permissions. Everything below assumes:

```bash
export NETBOX=https://netbox.example.com
export TOKEN=...
```

## Choosing which checks a policy requires

A policy's `checks` is a single list of names, holding both kinds:

- a **registered** opt-in check, which the plugin or another plugin declared in code;
- a **reported** check, whose name is yours to invent and which something outside NetBox reports a result for.

The form splits them into two boxes because only the first kind can be offered in a list. The API does not, so send them together:

```bash
curl -X PATCH "$NETBOX/api/plugins/change-control/policies/12/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"checks": ["peer-signoff", "cab-approval"]}'
```

`checks` replaces the whole list. To add one without dropping the rest, read it first:

```bash
CURRENT=$(curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/policies/12/" | jq -c '.checks')

curl -X PATCH "$NETBOX/api/plugins/change-control/policies/12/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d "{\"checks\": $(jq -cn --argjson c "$CURRENT" '$c + ["cab-approval"]')}"
```

Find the policies which already require a given check:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/policies/?check=cab-approval"
```

See [checks which do not apply everywhere](checks.md#which-checks-apply-and-where) for what each kind means.

## Reporting a check result

This is the half that unblocks a merge. Find the check on the change request, then report on it:

```bash
CHECK_ID=$(curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/checks/?change_request_id=$CR_ID&name=cab-approval" \
  | jq -r '.results[0].id')

curl -X PATCH "$NETBOX/api/plugins/change-control/checks/$CHECK_ID/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "success", "summary": "Approved at the 14:00 CAB", "details_url": "https://cab.example.com/2026-08-26"}'
```

`status` takes `pending`, `running`, `success`, `failure`, `error` or `skipped`. Set `running` when a job starts, so reviewers can see it is in progress.

A reporter cannot decide whether its own check counts. `required` is read-only on this endpoint, and the merge gate reads requiredness from the configuration and the policies rather than from the row, so the token your pipeline uses cannot neutralise the gate it reports to. `change_request` is read-only too: a result belongs to the request it was measured against.

## Reading a change request

The fields worth acting on:

| Field | Meaning |
|---|---|
| `status` | `draft`, `needs-review`, `approved`, `rejected` or `completed`. The people gate only. |
| `approved` | Whether the policies are satisfied. |
| `ready_to_merge` | Whether it can actually merge now. Approved is not the same thing: a check or the change window can still block. |
| `merge_blocked_reason` | Why not, in words, when `ready_to_merge` is false. |
| `has_conflicts` | Whether the branch genuinely conflicts with main. |
| `ref` | Your external reference, for correlating with a ticket or change record. |
| `branch` / `branch_name` | The branch, and its name kept after the branch is deleted. |
| `branch_deleted` | Whether the branch is gone and the record is history only. |

`ready_to_merge` is computed at read time, from the policies, the checks, the change window and the branch itself, so there is no filter for it. Narrow on what is stored and read the field from the results:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/change-requests/?status=approved&check_status=failure"
```

`ref` filters on a partial, case-insensitive match, and several values are an OR, so a pipeline can look a change request up by the ticket that spawned it:

```bash
curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/change-requests/?ref=CHG0012345"
```

## Submitting a review

```bash
curl -X POST "$NETBOX/api/plugins/change-control/reviews/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"change_request": 42, "decision": "approve", "comment": "Checked the interface descriptions."}'
```

Note the absent field. `reviewer` is read-only and always the caller, so a token cannot post an approval attributed to a colleague. Submitting one for somebody else is not an error; it is simply recorded as yours.

`decision` takes `approve`, `request-changes` or `comment`. Requesting changes needs a comment. A user cannot review their own change request, and a second review by the same user is refused: edit the existing one instead.

Reviews and comments accept Markdown, rendered through NetBox's sanitising filter.

## Commenting on one change

```bash
curl -X POST "$NETBOX/api/plugins/change-control/change-comments/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"change_request": 42, "change_diff": 907, "text": "Is this the right rack?"}'
```

`author` is read-only and always the caller, for the same reason `reviewer` is on a review: a comment is part of the record a reviewer reads before approving, so a token must not be able to post one under a colleague's name. Naming somebody else is not an error; the comment is simply recorded as yours.

`change_diff` has to be a change in the request's own branch. Crossing them is refused with a 400, because such a comment would be invisible on the tab it belongs to and counted as an open thread on a request it does not describe.

Reply within a thread by naming its root comment as `parent`. A reply must sit on the same change as its parent, and replies are one level deep: a reply to a reply joins the same thread.

## Events, the other direction

To be told when something happens rather than polling, use [event rules](event-rules.md).
