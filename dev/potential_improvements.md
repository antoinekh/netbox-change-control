# Potential improvements

Ideas that are not implemented, kept here so they are not rediscovered from scratch. Nothing on this page is promised.

Problems that belong to [netbox-branching](https://github.com/netboxlabs/netbox-branching) rather than to this plugin are in [`UPSTREAM.md`](UPSTREAM.md).

## Comments anchored to a field, not an object

A comment thread attaches to a `ChangeDiff`, which is one changed object. A reviewer who objects to one field of a device with twelve changed fields has to say which one in prose.

Anchoring a thread to `(diff, field)` would give the equivalent of a line comment on a pull request, and the Changes tab already renders per field, so the anchor exists in the markup. The work is in the model, the form and keeping a thread readable once the field is edited again.

## Recurring change windows

A change window is one fixed interval with dates, such as 25 November from 22:00 to 24:00. Many teams think in recurring slots instead: every night from 22:00 to 24:00, or every Tuesday night. Today an author who misses the slot has to edit the dates by hand, and an automatic merge that misses its window stops with a **Window closed before merge** history entry.

The proposed design (2026-11-24):

- A reusable schedule object, for example "Night window": the days of the week, a start time, an end time and a time zone. A change request points to a schedule instead of fixed dates, and many requests share one schedule.
- The merge gate checks the current time against the schedule. `window_state()` keeps its states: `open` inside an occurrence, `early` between occurrences. A schedule never reaches `closed`, so an automatic merge simply waits for the next occurrence.
- The sweep merges at the next opening. `unreliable_window` compares the length of one occurrence with `auto_merge_interval`.
- A later step: a schedule on a policy, such as "circuit changes only in the night window", so the policy sets the window and an author cannot choose a wider one.

Open questions: a schedule on the change request only, or also on a policy; one time zone for NetBox or one per schedule; fixed dates kept as an option beside schedules (recommended, for a one-time slot).

## Per-field conflict resolution

Today a conflict is reported and the reviewer resolves it by hand, by syncing or by editing one side until the two agree. There is no "keep mine / keep theirs" per field, and no record of what was decided.

This is mostly blocked upstream: branching offers acknowledgement, not resolution. See item 6 of [`UPSTREAM.md`](UPSTREAM.md).

## Checks shipped rather than documented

The plugin ships four checks (`has-changes`, `no-conflicts`, `not-stale`, `threads-resolved`). `enable_builtin_checks` makes them available, and a policy decides where they apply. Every other check is an example in [writing your own checks](../docs/custom-checks.md): to use one, a site writes its own NetBox plugin, copies the code, registers it in `ready()` and maintains it. Few NetBox teams do that.

The idea is to ship the most useful examples as built-in checks, so a site turns each one on with a setting and selects it in a policy, like the four built-ins. A policy decides where a check applies, so shipping a check no longer forces it on every change.

```python
PLUGINS_CONFIG = {
    'netbox_change_control': {
        'enable_builtin_checks': ['has-changes', 'no-conflicts', 'max-objects', 'ai-review'],
        'max_changed_objects': 50,
    },
}
```

### 1. A limit on the size of a change (`max-objects`)

The check fails when the branch changes more objects than the configured limit. A branch that bulk-edits 300 interfaces shows **Failed: 300 objects changed; the limit is 50. Split the change.** A "Core network" policy selects it, and a policy for cable labels does not, so a large label clean-up still merges.

It needs no external service and is deterministic, so it is the easiest one to ship. A check receives only the change request, so the limit is one global setting. A limit per policy (20 for core, 500 for labels) needs a parameter on the policy, which is more work.

### 2. A check that an external pipeline reports

Most of it works today: `required_external_checks` declares the check, the merge waits while it is pending, and the pipeline reports `status`, `summary` and `details_url` through the REST API. For example, a GitLab job renders the configurations of the changed devices from the branch, validates them (with Batfish, for example), and reports **Success: 12 configurations valid** with a link to the job.

What is missing is the signal that tells the pipeline when to start: today it polls, or the site builds its own event rule. Shipping it means a ready-made webhook when the checks must run again (the branch is synced, the change is edited), and GitLab CI and GitHub Actions recipes in the documentation.

### 3. An AI review (`ai-review`)

The check sends the diff to a model and shows its verdict, for example **Failed (advisory): the change disables ge-0/0/1, the only uplink of edge-sw-1**, or **Passed: descriptions and tags only.**

The rules that keep it safe:

- It is advisory by default (`required=False`), so it blocks a merge only where a site opts in.
- It never approves: approvals stay with people.
- Settings for the model, the API key (read from the environment) and a maximum diff size.

To solve before shipping:

- Cost and delay: checks run again on every refresh, including each change to a comment thread. This check must run only when the diff changes, or when someone presses **Re-run checks**.
- Data: the whole diff goes to a third party. The documentation must say so, and some sites will forbid it.
- Context: the model sees only the diff, not the rest of the network.

### Suggested order

1. `max-objects`: small, safe and useful at once.
2. The pipeline webhook and the CI recipes.
3. `ai-review`, after a check can run only when the diff changes.

## A calendar view of change windows

Change windows are per request and only visible on the request itself. A calendar of what is scheduled to merge, and when, is the view a change manager actually wants.

## Merge queue

Two approved branches that both touch the same objects will conflict, and the second one to merge finds out the hard way. A queue that merges in order and re-runs the checks between merges would catch that before it reaches main.
