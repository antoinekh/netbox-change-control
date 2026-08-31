# Changelog

## Unreleased

### Fixed

- **The policies governing a change request now follow the branch.** They were matched when the request was submitted and never again, except on a sync or a revert. A branch which grew a new object type after submission kept the policies matched against its old contents, so an author could open a request on a low-risk branch, collect the light approval that attracted, then add the real change to the same branch and merge it under that same approval. Policies are now re-matched whenever the branch starts touching something new, and the merge gate re-matches for itself rather than trusting that signal to have fired.

### Added

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
