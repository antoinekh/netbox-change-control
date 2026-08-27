# Changelog

## Unreleased

### Fixed

- Pre-merge checks sat at **pending** until somebody pressed Re-run. A change request is created before its policies are matched, and every built-in check is policy-scoped, so the run at creation found no checks to run. Attaching or detaching a policy now runs them.
- The approval panel repeated each rule's shortfall in prose below a table that already showed the same counts. The page now shows only what the table cannot: a rejection, stale reviews, or no rules applying at all. The merge gate and the REST API still get the full text, having no table to read.
- The review form's Markdown hint listed examples the live preview already demonstrates.

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
