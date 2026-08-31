# Changelog

## Unreleased

### Fixed

- **The policies governing a change request now follow the branch.** They were matched when the request was submitted and never again, except on a sync or a revert. A branch which grew a new object type after submission kept the policies matched against its old contents, so an author could open a request on a low-risk branch, collect the light approval that attracted, then add the real change to the same branch and merge it under that same approval. Policies are now re-matched whenever the branch starts touching something new, and the merge gate re-matches for itself rather than trusting that signal to have fired.

- **The REST API no longer lets a caller forge the author of a change comment.** `author` was writable, so any token holding `add_changecomment` could post a comment attributed to a colleague, faking a sign-off in the discussion a reviewer reads before approving. It is now read-only and always the caller, matching `reviewer` on a review.
- **Automatic merge no longer queues two jobs for one branch.** Becoming mergeable can be reached by more than one route in a single write, and the status is still Approved at the second arrival because the merge has only been queued rather than run. The first job merged and the second then failed with "not ready to merge", which read as a broken merge on a change that had in fact gone through. Auto-merge now refuses to queue while a merge for that branch is already pending, scheduled or running.
- The cached list columns follow three events they were missing: a check result reported over the REST API, which is the usual way a pipeline clears the last gate; a branch merging, after which the list went on offering the change as ready to merge; and a conflict appearing on a request whose policies do not require the `no-conflicts` check.
- **The change request list no longer gets slower as it gets longer.** The **Conflicts** and **Ready to merge** columns were computed per row, at roughly eleven queries each, so a default page of fifty cost about five hundred queries for two columns. Both now read a cached field refreshed on the events that change them, which is the split the plugin already makes for `status`: a cache for display and filtering, never for a decision. The merge gate and the change request page still recompute in full. Both columns are now sortable and filterable as well.
- Four badges used `text-bg-grey`, which NetBox does not define, so they rendered with no background at all. A test now checks every badge colour in every template against the set NetBox ships.
- The review form opens on the reviewer's standing decision. It prefilled the comment but reset the decision to **Approve**, so a reviewer returning to amend a **Request changes** was silently offered an approval instead.
- A review's own page now shows whether it has gone stale, which until now was visible only on the change request.
- The Changes tab formats comment timestamps the same way the rest of the interface does.
- List pages no longer offer **Import**, and reviews and merge checks no longer offer **Add** or **Edit Selected**. This plugin routes none of those, and NetBox rendered the buttons anyway with the literal string `None` as their target, so clicking one gave a 404.
- **Submit for review** now requires `change_changerequest`. It checked nothing at all, so any signed-in user could push somebody else's draft into review, attaching its policies and announcing `change_request_submitted` to every event rule watching for it.
- **Check results are recorded in the changelog.** They were written with a queryset update, which goes straight to the database and fires no `post_save`, so NetBox recorded nothing: a required check going from failed to passed, which is what opens the merge gate, left no entry at all. A re-run that finds the same answer still writes nothing, so the log holds transitions rather than noise.
- Renaming a branch now updates the name stored on its change request. That copy exists so the record stays readable once the branch is gone, but it is also what the branch filter and the global search read, so a renamed branch could not be found by the name shown on its own page.
- A reply to a reply is now flattened into its thread on every path, not only in the interface. The flattening ran in `clean()`, which the REST serializer discards, so a reply posted over the API was stored as a grandchild and the Changes tab, which builds threads from their roots, rendered it nowhere at all.
- A change comment naming a change that no longer exists now reports a validation error rather than a server error.
- A change comment can no longer refer to a change in another request's branch. The Changes tab already looked the change up scoped to the branch, but the REST API took both as plain ids, and such a comment was invisible on the tab it belonged to while counting as an open thread on a request it did not describe.

- **A change request's status can no longer be set by hand.** It is derived from the policy evaluation, but it was writable on the bulk edit form and over the REST API, and Completed is terminal: the merge gate refuses a completed request and nothing reopens one, so one bulk edit blocked a branch from merging for good with no way back through the interface. `status` is now read-only on the API and gone from the bulk edit form.

### Added

- **Global search.** Change requests, policies, rules, reviews, checks and comments now appear in NetBox's search box; none of them did before, because the plugin registered no search index at all. A change request is found by its reference first, so a ticket number typed into the search box finds the change it spawned, which is what the `ref` field exists for. A request whose branch has been deleted is still found by the branch's name, which is when search is the only way left to reach it.
- [Permissions](docs/permissions.md) is now a complete reference: every permission the plugin defines, in a table per model, with the action to enter on the permission form and what it grants. It previously said "and friends" and left the reader to guess. A test compares the page against the model definitions, so neither can drift from the other.
- **Abandon** and **Reopen** actions on a change request, each behind its own permission (`abandon_changerequest`, `reopen_changerequest`), on the change request page and as `POST /change-requests/{id}/abandon/` and `/reopen/`. These are the two transitions a person legitimately makes now that status is not an editable field. Reopening recomputes rather than restoring, because reviews go stale and policies change while a request is set aside.
- An [administration guide](docs/admin-guide.md): roles, a permission matrix for every model, how to narrow a permission with a constraint, building policies, an end-to-end test procedure and troubleshooting.

### Removed

- The `lock_matched_policies` setting, which was documented but read nowhere, so turning it off changed nothing. Policies are matched from the branch contents and there is no interface for attaching or detaching one by hand, which is what it claimed to control.

### Changed

- Every built-in check now reports **skipped** on a change request whose branch has been deleted. `threads-resolved` failed instead, because comment threads deliberately outlive the branch, so the record of a change nobody can merge any more was marked as blocked for ever.
- `docs/api.md` gave the wrong filter for finding policies by check (`check`, not `required_checks`) and the wrong value for requesting changes (`request-changes`, not `reject`).
- `docs/design.md` named a permission that does not exist, was dated at 0.1.0, listed five of the seven models, and pointed readers at a development harness which is not part of this repository.
- `docs/checks.md` was missing two of the moments checks run: a policy attaching or detaching, and a branch diff changing.
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
