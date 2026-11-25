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

The check registry is the intended extension point, and [writing your own checks](../docs/custom-checks.md) shows an AI reviewer and a CI reporter as worked examples. Both are examples, not features: neither ships. Now that a policy decides where a check applies, shipping one is safer than it was, because a site chooses which changes it governs rather than getting it everywhere.

Candidates for shipping, each behind its own setting:

- a model-backed review of the diff, reporting a summary and a pass or fail;
- a check that waits on an external pipeline and is reported through the REST API;
- a check that refuses a change touching more than a configured number of objects.

The AI one needs care. A model must not be able to approve a change on its own, so it should report as an advisory check by default, and a site should have to opt in to making it required.

## A calendar view of change windows

Change windows are per request and only visible on the request itself. A calendar of what is scheduled to merge, and when, is the view a change manager actually wants.

## Merge queue

Two approved branches that both touch the same objects will conflict, and the second one to merge finds out the hard way. A queue that merges in order and re-runs the checks between merges would catch that before it reaches main.
