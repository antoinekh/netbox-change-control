# Conflicts with main

A conflict means main has changed the same fields as your branch. Merging through one silently discards somebody's work, so a conflict blocks the merge through the `no-conflicts` check and shows a red banner on the change request. The plugin reads the conflicts netbox-branching records, so the change request and the branch page always agree.

## What branching flags

`netbox-branching` records three snapshots of every changed object in a `ChangeDiff`:

| Field | Meaning |
|---|---|
| `original` | the baseline: the object as it was when the branch first changed it, or as main had it at the last sync |
| `modified` | where your branch has ended up |
| `current` | where main has ended up |

It flags a conflict when a field differs from `original` on **both** sides, and the two sides disagree.

## Syncing moves the baseline

A sync brings main's changes into the branch and moves `original` to main's state. So consider this ordinary sequence:

```
07:15  branch   device_type  6 → 11    you change it in your branch
07:59  main     device_type  6 → 5     someone changes it in main: conflict
08:00  sync                  11 → 5    you sync; main's value lands in your branch, and the baseline moves to 5
08:01  branch   device_type   5 → 12   you change it again: no conflict
```

After the sync your branch holds main's value, and your later edit is not flagged. In git terms that is a rebase followed by a commit: there is nothing to reconcile.

!!! note

    netbox-branching moves the baseline on sync from 1.2.2, which is why this plugin requires that version ([#640](https://github.com/netboxlabs/netbox-branching/issues/640)). Before it, the baseline never moved, and a field main touched before the sync stayed flagged for the life of the branch. Upgrading the plugin clears those stale flags once, in a migration.

## Resolving a conflict

Branching has no merge-conflict editor. It does not blend the two values. Either accept that the branch's value overwrites main, or change one side so they agree.

1. **Sync the branch.** The branch adopts main's value, which replaces your edit to that field, and the conflict clears. Make the edit again on top of it if you still need it.
2. **Accept the branch value.** Merge from the branch page and tick the conflict acknowledgement. Main takes your branch's value; main's competing change is discarded. Do a dry run first by leaving **Commit changes** unticked.
3. **Make the two sides agree.** Edit the object in main to match your branch, or activate the branch and edit it there to match main. Either way the conflict clears on its own, and the banner and the check follow immediately.

Syncing only replays changes newer than the last sync. If main has not moved since your last sync, syncing again does nothing.
