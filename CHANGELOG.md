# Changelog

## Unreleased

### Fixed

- **The policies governing a change request now follow the branch.** They were matched when the request was submitted and never again, except on a sync or a revert. A branch which grew a new object type after submission kept the policies matched against its old contents, so an author could open a request on a low-risk branch, collect the light approval that attracted, then add the real change to the same branch and merge it under that same approval. Policies are now re-matched whenever the branch starts touching something new, and the merge gate re-matches for itself rather than trusting that signal to have fired.

- **The REST API no longer lets a caller forge the author of a change comment.** `author` was writable, so any token holding `add_changecomment` could post a comment attributed to a colleague, faking a sign-off in the discussion a reviewer reads before approving. It is now read-only and always the caller, matching `reviewer` on a review.
- **Automatic merge no longer queues two jobs for one branch.** Becoming mergeable can be reached by more than one route in a single write, and the status is still Approved at the second arrival because the merge has only been queued rather than run. The first job merged and the second then failed with "not ready to merge", which read as a broken merge on a change that had in fact gone through. Auto-merge now refuses to queue while a merge for that branch is already pending, scheduled or running.
- Renaming a branch now updates the name stored on its change request. That copy exists so the record stays readable once the branch is gone, but it is also what the branch filter and the global search read, so a renamed branch could not be found by the name shown on its own page.
- A reply to a reply is now flattened into its thread on every path, not only in the interface. The flattening ran in `clean()`, which the REST serializer discards, so a reply posted over the API was stored as a grandchild and the Changes tab, which builds threads from their roots, rendered it nowhere at all.
- A change comment naming a change that no longer exists now reports a validation error rather than a server error.
- A change comment can no longer refer to a change in another request's branch. The Changes tab already looked the change up scoped to the branch, but the REST API took both as plain ids, and such a comment was invisible on the tab it belonged to while counting as an open thread on a request it did not describe.

- **A change request's status can no longer be set by hand.** It is derived from the policy evaluation, but it was writable on the bulk edit form and over the REST API, and Completed is terminal: the merge gate refuses a completed request and nothing reopens one, so one bulk edit blocked a branch from merging for good with no way back through the interface. `status` is now read-only on the API and gone from the bulk edit form.

### Added

- **Abandon** and **Reopen** actions on a change request, each behind its own permission (`abandon_changerequest`, `reopen_changerequest`), on the change request page and as `POST /change-requests/{id}/abandon/` and `/reopen/`. These are the two transitions a person legitimately makes now that status is not an editable field. Reopening recomputes rather than restoring, because reviews go stale and policies change while a request is set aside.
- An [administration guide](docs/admin-guide.md): roles, a permission matrix for every model, how to narrow a permission with a constraint, building policies, an end-to-end test procedure and troubleshooting.

### Changed

- [Permissions](docs/permissions.md) now states plainly that a NetBox object permission covers every object of its type, so `delete_review` lets a user delete anybody's review, and shows the constraint that narrows it.
- The **Conflicts** column on the change request list now matches netbox-branching: a red octagon when there are conflicts, a dash when there are none.
- The README now warns that the plugin is below 1.0 and that models, settings and the REST API can still change.
- Improved the docs.

## 0.2.0 - 2026-08-27

### Added

- A `ref` field on a change request, for a ticket id or change number. Shown in the list, searchable, filterable and in the REST API.
- **Condition state** on a policy. Conditions now read both sides of a change by default, so `status == active` catches an object being switched off as well as one being switched on. Set it to `after` or `before` to narrow that back.

### Fixed

- Pre-merge checks stayed **pending** until somebody pressed Re-run. Attaching or detaching a policy now runs them.
- The approval panel repeated in prose what its own table already showed.

## 0.1.0 - 2026-08-27

First release. Policy-driven change control and mandatory review for NetBox branches.

- Policies carrying rules that set a minimum number of approvals and who may give them, attached to a change request automatically from the object types the branch touches, and locked against its author.
- Optional conditions narrowing a policy on the values of the changed objects.
- Rules requiring zero approvals, for changes gated by their checks alone.
- Change requests, one per branch, with status, priority and a change window.
- Reviews, one decision per reviewer, and comment threads anchored to individual changed objects.
- A merge gate registered with netbox-branching, so a blocked branch cannot merge and does not offer the button.
- Pre-merge checks gating the merge independently of the reviews. Four built in; more can be registered in process or reported from a pipeline over the REST API.
- Policies, rather than configuration, decide which checks apply.
- Change windows with an override permission, and automatic merging once every gate is satisfied.
- `protect_main`, refusing writes outside a branch, optionally scoped to named models.
- Stale approvals, dropped when the branch moves on, and policy re-evaluation when rules or group membership change.
- Conflicts with main judged against what the branch has actually seen.
- Change requests that outlive their branch, keeping the reviews, threads and results.
- Six lifecycle events on NetBox's event pipeline, reviewer notifications, and a **My Reviews** dashboard widget.

See [`docs/`](docs/index.md) for how any of it works.
