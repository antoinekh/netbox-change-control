# Screenshots

## Captured

These are in place and referenced from the pages listed.

| File | Page | Shows |
|---|---|---|
| `change-request-detail.png` | `README.md` | The change request page: approval status with each rule and who may approve it, the pre-merge checks, the disabled merge button with its reason, and the applied policies. |
| `change-request-list.png` | `docs/change-requests.md` | The change request columns, including **Conflicts** and **Ready to merge**. Captured through a NetBox Object List dashboard widget. |
| `policy-list.png` | `docs/policies.md` | All five seeded policies with their scope, rule count and enabled state. |
| `policy-detail.png` | `docs/policies.md` | One policy: its scope and its rules. |
| `policy-rule-edit.png` | `docs/policies.md` | The rule form: minimum reviews, reviewer groups and individual reviewers. |
| `changes-tab.png` | `docs/reviews.md` | The Changes tab with related objects resolved to names, for example `ISR 1111-8P (#6) -> QFX5110-48S-4C (#12)`. |
| `merge-gate-blocked.png` | `docs/merging.md` | The branching merge form refusing a branch that has no change request, offering only a dry run. |
| `merge-checks.png` | `docs/checks.md` | The Pre-merge checks panel: three passing, `threads-resolved` failing, and the Re-run checks button. |
| `merge-button.png` | `docs/merging.md` | Every rule satisfied, every check passed, and an active **Merge branch** button. |
| `my-reviews-widget.png` | `docs/automation.md` | The My Reviews dashboard widget listing the two requests waiting on that user. |
| `protect-main-blocked.png` | `docs/protect-main.md` | A write to main refused, carrying the reason and what to do instead. |

Every page has its image. Nothing is outstanding.

## Retaking one

Run `make seed` once, then `make captures`. It builds the change-request states and prints the URL, the user and what to frame for each shot. Both live in the local development harness, which is not versioned.

`protect-main-blocked.png` needs no state: sign in as `erin`, edit a circuit provider and save.

Seeded users have the password `demo1234`. Use a private window, because a superuser is never blocked by `protect_main` and never sees the review form on a request they opened.

## Capture settings

Roughly 1400px wide, light mode, with the demo data loaded so the screenshots contain realistic object names. Crop to the relevant card rather than the whole browser window, except for `change-request-detail.png` and `my-reviews-widget.png`, which are meant to show context.
