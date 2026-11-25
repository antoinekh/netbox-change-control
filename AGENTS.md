# AGENTS.md

netbox-change-control is a NetBox 4.7 plugin that puts a policy-driven review gate in front of netbox-branching merges. A change request follows one branch from draft to merge, and its record outlives the branch.

## Find the architecture

- `docs/design.md`: the decisions and why (the gate is a validator, eligibility is per rule, zero rules fail closed, plugin models are exempt from branching), the data model, and the module layout. Read it before you change a rule.
- `docs/change-requests.md`, section "Every transition": the specification of the change request state machine. `tests/test_state_machine.py` parses this table.
- `dev/UPSTREAM.md`: the netbox-branching problems that the plugin works around. Read it before you change or remove a workaround.
- `dev/DEV.md`: the development stack in `dev/stack/`, the `make` targets, the seeded users and policies. The stack is for development and tests only, never for production: its credentials are public, so its ports listen on localhost.
- The other pages in `docs/` are the user documentation, one page per feature. The code follows them: when a behaviour changes, its page changes in the same commit.

Where each state lives:

| State | Owner | Guards and rules |
|---|---|---|
| Change request status and its history (`HistoryEntry`) | `lifecycle.py`, the only writer | `can_be_*`, `is_*` and `clean()` on `ChangeRequest` in `models/requests.py` |
| Derived status (Needs review, Approved, Rejected) and staleness | `lifecycle.refresh_status`, from `policy.py` | `policy.evaluate_change_request`, `policy.last_change_time` |
| Attached policies | `policy.sync_policies` | `ChangeRequest.policies_follow_branch` |
| Merge gate | `validators.py`, registered with netbox-branching | Re-evaluates the policies; it never trusts the stored status |
| Check results | `checks.run_checks` (registered checks) and the reporter (reported checks) | `MergeCheck.clean()` and `save()` in `models/checks.py` |
| Automatic merge stops | `automerge.stopped`, returns an `AutoMergeStop` | `lifecycle.merge_failed_since_approval` |
| Change window | `ChangeRequest.window_state()`, returns a `ChangeWindowState` | A window move records **Window changed** and makes the reviews stale (`receivers/status.py`) |
| Changes shown for a request | `ChangeRequest.changes()`, from `changes_source` (`ChangesSource`) | The branch diff, else the merge record (`MergedChange`) |

The state names live in `choices.py` (NetBox `ChoiceSet` for stored values, `StrEnum` for computed states). `receivers/` holds one module per event source: branch merge, revert and deletion, failed merge jobs, branch edits, policy and membership changes, `protect_main`.

## Explicit state only

Every field or flag that moves through states is a state machine, and it is written explicitly:

- Name its states with a `ChoiceSet` or a `StrEnum` in `choices.py`. Templates and views compare against those names, through a model property where possible.
- Give it one owner. Views, the REST API, forms, signal receivers and jobs call the owner; the field is read-only everywhere else.
- Each transition locks the row (`lock_status()`), checks a guard on the model, writes, records a `HistoryEntry` and announces once (notification and event).
- Ask for a target state, never a toggle: a form or an API call names the state it wants, so a double click or a stale page cannot undo it.
- Give every state an exit. Refuse impossible combinations of fields in `clean()`, and repair old rows with a data migration.
- Keep each rule in one function, and update the transition table in `docs/change-requests.md` in the same commit as the code.
- A finished request (Completed, Abandoned) is a record: its `RECORD_FIELDS`, check results and policies stay as they ended.

## Tests

End-to-end first. For behaviour a person sees, extend a journey in `e2e/test_journeys.py` or add one: it drives a running NetBox through the browser (Playwright) as a requester, a reviewer and a viewer. `make e2e` runs the journeys against a NetBox of their own, and `.github/workflows/e2e.yaml` runs them in CI.

Add a unit test in `netbox_change_control/tests/` only for what a journey cannot reach cheaply: concurrency, migrations, query counts, who may write what, and rules with many cases (one table test with `subTest`, not one test per case). A new status transition is covered by the state machine test once the docs table lists it; add a scenario to `SCENARIOS` if no scenario reaches the new row.

Keep the suite lean: obvious code (properties, labels, `__str__`, a page that returns 200), NetBox's own behaviour and a rule already tested at another layer need no test. Coverage is not a goal, and CI time is not a concern.

A test earns its place by going red: remove the fix, see the test fail, put the fix back.

`make test` runs the unit tests in the development stack; `.github/workflows/tests.yaml` runs them in CI with `dev/ci/configuration.py`. Most of them build branches without a schema; `tests/base.py` holds the shared fixtures.

## Conventions

- Every change gets a bullet under `## Unreleased` in `CHANGELOG.md`, in its section (Upgrading, Changed, Fixed, Removed, Documentation).
- netbox-branching runs the plugin migrations again in each branch schema, so a data migration skips what is already done.
- Lint with `make lint`, which runs ruff as CI does.
- A code comment says why, in one sentence.
