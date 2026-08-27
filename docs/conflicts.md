# Conflicts with main

A conflict means main has changed the same fields as your branch. Merging through one silently discards somebody's work, so a real conflict blocks the merge through the `no-conflicts` check and shows a red banner on the change request.

Not every conflict the branching plugin reports is real. This page explains the difference, because the plugin deliberately disagrees with the branch page about it.

## What branching flags

`netbox-branching` records three snapshots of every changed object in a `ChangeDiff`:

| Field | Meaning |
|---|---|
| `original` | the object as it was **when the branch was created** |
| `modified` | where your branch has ended up |
| `current` | where main has ended up |

It flags a conflict when a field differs from `original` on **both** sides.

## Why that over-reports

`original` is written once, when the diff is first created, and is **never advanced**. A sync does not move it.

So consider this ordinary sequence:

```
07:15  branch   device_type  6 → 11    you change it in your branch
07:59  main     device_type  6 → 5     someone changes it in main
08:00  sync                  11 → 5    you sync; main's value lands in your branch
08:01  branch   device_type   5 → 12   you change it again
```

After the sync your branch held main's value. You then made one more edit. In git that is a fast-forward: there is nothing to reconcile.

Branching still reports a conflict, because `original` is stuck at 6 while `modified` is 12 and `current` is 5. It will report it forever, however many times you sync, until main or the branch happens to land back on 6.

## What this plugin does instead

A conflict is real only when **main has moved since the branch last synced**. If main has not changed the object since the sync, your branch already contains main's value and merging cannot discard anything.

So the plugin cross-references each flagged object against `Branch.get_unsynced_changes()`:

| Situation | Branching says | This plugin says |
|---|---|---|
| Main changed the object **after** the last sync | conflict | **conflict**: red banner, `no-conflicts` fails, merge blocked |
| Main changed it **before** the last sync, and the branch edited it after | conflict | **reconciled**: an informational note, `no-conflicts` passes, merge allowed |
| Nothing flagged | clean | clean |

A reconciled flag shows on the change request as a note, not an alarm:

> The branching plugin flags 1 object as conflicting, but a sync has already reconciled it. Main has not changed these objects since this branch last synced, so merging cannot discard anything. The branch page will still ask you to acknowledge them.

The branch page under **Branching > Branches** is unaffected and will still ask you to acknowledge the flag on the merge form. That is branching's own screen, and this plugin does not modify it.

## Why not just fail on everything branching flags

Because it would train reviewers to acknowledge conflicts by reflex, which is precisely what must not happen when a real one appears. A gate that cries wolf is worse than no gate.

## Resolving a real conflict

Branching has no merge-conflict editor. It does not blend the two values. Either accept that the branch's value overwrites main, or change one side so they agree.

1. **Accept the branch value.** Merge from the branch page and tick the conflict acknowledgement. Main takes your branch's value; main's competing change is discarded. Do a dry run first by leaving **Commit changes** unticked.
2. **Make the two sides agree.** Edit the object in main to match your branch, or activate the branch and edit it there to match main. Either way the conflict clears on its own, and the banner and the check follow immediately.

Syncing does not resolve a conflict on its own. It applies main's value onto the branch, which clears the flag at that moment, and it only replays changes newer than the last sync. If main has not moved since your last sync, syncing again does nothing.

> [!NOTE]
> We would rather delete this code. If `netbox-branching` advanced a diff's baseline on sync, the way git moves the merge base, its own flag would be correct and this plugin could simply trust it.
