# Change requests

One change request per branch.

![Change request list](img/change-request-list.png)

| Field | Meaning |
|---|---|
| Branch | The branch this request proposes to merge. |
| Reference | An external reference: a ticket id, a change number, whatever your process uses. Optional, shown in the list, searchable and filterable. |
| Title | A short summary. |
| Description | A longer explanation of what is changing and why. Shown in the list and on the detail page. |
| Status | Draft, Needs review, Approved, Rejected, Completed or Abandoned. |
| Priority | Low, Medium, High or Critical. |
| Requester | Who opened it. |
| Window opens / closes | An optional [change window](merging.md#change-windows). |
| Merge automatically | Merge without further human action once every gate passes. See [automatic merging](merging.md#automatic-merging). |

Status is derived from the policy evaluation and is refreshed automatically, so it is not a field you set. It is read-only on the REST API and absent from the bulk edit form, because Completed is terminal: the merge gate refuses a completed request and nothing reopens one, so setting it by hand blocked its branch from merging for good.

The two transitions a person makes by hand have an action each, and a permission each.

| Action | Where | Permission | Allowed from |
|---|---|---|---|
| **Abandon** | The change request page, or `POST /change-requests/{id}/abandon/` | `netbox_change_control.abandon_changerequest` | Draft, Needs review, Approved, Rejected |
| **Reopen** | The change request page, or `POST /change-requests/{id}/reopen/` | `netbox_change_control.reopen_changerequest` | Abandoned only |

Reopening returns the request to Draft and then recomputes, rather than restoring the status it held before. Its reviews may have gone stale and its policies may have changed while it was set aside, so the honest answer has to be worked out again.

Completed is never reopened. It records a merge that actually happened, and taking it back up would invite a second merge of a branch already in main.

## Approved is not the same as mergeable

**Approved** means the people gate is satisfied: every policy rule has its approvals. It does not mean the change can go ahead. Checks and the change window are separate gates, so a request can be approved and still blocked, for example by an unresolved comment thread.

The change request page shows both. The status badge reads `Approved`, and beside it a `Blocked` badge appears with the reason whenever the change cannot actually merge:

> **Approved** · **Blocked**
> Approved by reviewers, but not yet mergeable. Required checks are not passing: Comment threads resolved (failed).

The REST API exposes `ready_to_merge` and `merge_blocked_reason` alongside `approved`, and this page's own badge reads the same source. All of them delegate to netbox-branching, so they account for every gate, including validators registered by other plugins.

The change request **list** answers the same question from a cache, because computing it per row cost about eleven queries and a page holds fifty. The cache is refreshed whenever anything that could change the answer happens: a review, a policy, a check, a branch sync, a new change in the branch. The change window is the exception, since a window opens because the clock moved rather than because anything happened, so it is evaluated as the row is rendered from fields already loaded.

Two consequences worth knowing. The list does not account for merge validators registered by other plugins, which this page does. And the list is what you sort and filter on, which the live version could never support. Where the two could disagree, this page is authoritative, and the merge gate itself never reads the cache at all.

## The record outlives the branch

A change request is the record of who approved what, so it survives deletion of its branch. The branch link is cleared, the branch **name** is kept and follows the branch through any rename, and the title, description, reviews, comment threads, applied policies and check results all remain. Each comment also keeps the name of the object it was about, so the discussion still makes sense once the diff is gone.

Such a request shows its branch name with a **Deleted** badge and is a historical record only: it cannot be merged, its diff is gone, and its checks report as skipped rather than failing. The REST API exposes `branch_name` and `branch_deleted` alongside `branch`.
