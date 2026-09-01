# Changelog

## 0.3.0 - 2026-09-01

### Added

- **Return to draft.** A submitted change request can be pulled back out of review, by its author or anybody holding `change_changerequest`, from the page or `POST /change-requests/{id}/return-to-draft/`. The reviews are kept, and resubmitting picks up where it left off. Allowed from Approved too, so an author who spots a problem after approval need not race the merge; it only ever closes the merge gate.
- **Abandon** and **Reopen**, each behind its own permission (`abandon_changerequest`, `reopen_changerequest`), on the page and as `POST /change-requests/{id}/abandon/` and `/reopen/`. With **Submit for review** and **Return to draft** these are the four transitions a person makes; everything else is derived. `submit` is a REST action too, so an integration can drive the whole lifecycle now that `status` is read-only. [The lifecycle diagram](docs/change-requests.md#the-lifecycle) shows the rest.
- **Draft is a state the author holds** rather than one the evaluation computes. A review submitted against a draft moves nothing, and a request pulled back stays pulled back; without this, **Return to draft** would be undone by the next signal. Submitting is what leaves draft, matches the policies and notifies the reviewers.
- **The branch page shows its change request**: status, reference, what is outstanding and who can clear it, with a link. A branch with no change request is told it cannot merge until one is opened, with a button to open it, which is the case netbox-branching's merge form could only refuse.
- `branch_page_placement` decides where that panel sits: `right_page` for a card in the right-hand column, which is the default, `alerts` for a band across the top, both for both, `[]` for neither. Both render the same wording, so they cannot drift apart.
- **Global search** over change requests, policies, rules, reviews, checks and comments; the plugin registered no index at all before. A request is found by its reference first, so a ticket number finds the change it spawned, and one whose branch has been deleted is still found by the branch's name.
- A change comment can be **edited and deleted from the interface**. There was no view for either, so a typo in a review comment was permanent. Editing is restricted to the author, as a review is.

### Fixed

- **The policies governing a change request follow the branch.** They were matched at submission and never again, so an author could open a request on a low-risk branch, collect the light approval that attracted, then add the real change to the same branch and merge it under that approval. They are re-matched whenever the branch touches something new, and the merge gate re-matches for itself.
- **A change request's status can no longer be set by hand.** It is derived, but it was writable on the bulk edit form and over the REST API, and Completed is terminal, so one bulk edit blocked a branch from merging for good. It is now read-only on the API and gone from the form.
- **Submit for review** requires `change_changerequest`. It checked nothing, so any signed-in user could push somebody else's draft into review, attaching its policies and announcing the event.
- **The REST API no longer lets a caller forge the author of a change comment.** `author` was writable, so any token holding `add_changecomment` could fake a colleague's sign-off in the discussion a reviewer reads before approving. It is now always the caller, matching `reviewer` on a review.
- **Automatic merge no longer queues two jobs for one branch.** Becoming mergeable can be reached by two routes in one write, and the second arrival still saw Approved because the first had only queued. The second job then failed with "not ready to merge" on a change that had gone through. Auto-merge refuses to queue while a merge for that branch is pending, scheduled or running.
- **Check results reach the changelog.** They were written with a queryset update, which fires no `post_save`, so a required check going from failed to passed left no entry. A re-run finding the same answer still writes nothing.
- Every built-in check reports **skipped** on a change request whose branch is gone. `threads-resolved` failed instead, because threads outlive the branch, so a change nobody can merge was marked blocked for ever.
- A reply to a reply is flattened into its thread on every path. The flattening ran in `clean()`, which the REST serializer discards, so a reply posted over the API was stored as a grandchild and rendered nowhere at all.
- A change comment can no longer name a change in another request's branch, which was invisible on the tab it belonged to while blocking a request it did not describe, and one naming a change that no longer exists reports a validation error rather than a server error.
- Renaming a branch updates the name stored on its change request, which is what the branch filter and the search read, so a renamed branch could not be found by the name shown on its own page.
- List pages no longer offer **Import**, and reviews and merge checks no longer offer **Add** or **Edit Selected**. The plugin routes none of those and NetBox rendered them with `None` as their target, so clicking one gave a 404.
- Four badges used `text-bg-grey`, which NetBox does not define, so they rendered with no background. A test now checks every badge colour against the set NetBox ships.
- The review form opens on the reviewer's standing decision. It reset it to **Approve**, so a reviewer amending a **Request changes** was silently offered an approval.
- A review's own page says whether it has gone stale, and the Changes tab formats timestamps the way the rest of the interface does.
- The second line of the reconciled-conflicts and change-window alerts sits under the sentence it explains rather than beside it. An alert lays its direct children out in a row, so the detail was rendered as a column of its own.

### Changed

- **The change request list no longer gets slower as it gets longer.** **Conflicts** and **Ready to merge** were computed per row, about five hundred queries for a page of fifty. Both now read a cached field refreshed on the events that move them, the split the plugin already makes for `status`: a cache for display and filtering, never for a decision. The merge gate and the change request page still recompute in full. Both columns are now sortable and filterable, and **Reviews** no longer costs a query per row.
- **One user action refreshes a change request once**, not once per policy. A request governed by three policies recomputed its status and ran every check four times for an identical answer. Nothing is deferred past the commit.
- **A rule names its groups rather than expanding them into members.** A group of fifteen printed fifteen usernames on every rule it satisfied, burying the approval counts that are the point of the panel, and went stale as people joined and left. An empty group is still called out by name, because such a rule can never be satisfied.
- All four lifecycle actions sit on the control bar beside Edit and Delete. Submit and Abandon sat in the Applied policies card, where they read as something to do with the policies; that card now carries only policies.
- **Reopening an abandoned request returns it to Draft** rather than straight to review: its reviews may be stale and its policies may have moved while it was set aside.
- The **Conflicts** column matches netbox-branching: a red octagon when there are conflicts, a dash when there are none.
- Packaging: the build no longer pulls `setuptools-scm`, which it never read because the version is written by hand, and the package declares `Development Status :: 4 - Beta`, which is what the README says in prose.
- CI installs the plugin editable, so the tests that compare a documentation page against the code can find that page. A plain install copies the package into `site-packages` with no `docs/` beside it, and those tests could only error there.
- [Permissions](docs/permissions.md) is a complete reference, with a test comparing it against the models, and there is an [administration guide](docs/admin-guide.md). The README warns that the plugin is below 1.0. `docs/api.md`, `docs/design.md` and `docs/checks.md` are corrected: a wrong policy filter, a wrong value for requesting changes, a permission that does not exist, and two of the moments checks run.

### Removed

- **The policy binding table defines no permissions.** Django creates four for every model and no view reads any of them, so granting `delete_changerequestpolicy` to detach a policy by hand only got the binding back at the next re-match. Change the policy's scope instead.
- `ChangeRequestPolicy.matched` and `created`, and `Policy.applies_to_all_object_types`, none of which anything read. `matched` was always true, so the filter reading it was a no-op and the **Auto** badge it drove appeared on every policy.
- The `lock_matched_policies` setting, documented but read nowhere, so turning it off changed nothing. Nothing can attach or detach a policy by hand, which is what it claimed to control.
- `ReviewBulkEditForm`, used by no view, and the `INTERVAL_*` re-exports in `jobs.py`, referenced by nothing.

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
