# Upstream: netbox-branching

Things this plugin works around in [netbox-branching](https://github.com/netboxlabs/netbox-branching). Each one is code we would rather delete.

Verified against netbox-branching 1.1.3 and NetBox 4.6.8. Items 2, 3 and 4 are still present in the netbox-branching 1.2.3 code and on its main branch (8b2d425).

---

## 1. Sync does not advance the conflict baseline (FIXED in 1.2.2)

**Status:** fixed upstream by [#660](https://github.com/netboxlabs/netbox-branching/pull/660) for [#640](https://github.com/netboxlabs/netbox-branching/issues/640), released in 1.2.2. Kept here because it is why this plugin requires that version.

`ChangeDiff.original` was written once, when the diff was created, and a sync never moved it. Main's change then kept reading as divergence for the life of the branch, and syncing again could not clear it.

1.2.2 moves `original` to main's state for every object a sync applies a change to, and recomputes `conflicts`. The plugin now reads `ChangeDiff.conflicts` directly. Migration `0009` clears, once, the stale flags recorded before the upgrade, which a later sync does not touch.

---

## 2. `altered_in_current` crashes on an object created in a branch

**Severity:** medium. Produces an HTTP 500.

`ChangeDiff.altered_in_current` tests membership in `self.original` without checking whether it is `None`:

```python
@cached_property
def altered_in_current(self):
    if self.current is None:
        return set()
    return {
        k for k, v in self.current.items()
        if k in self.original and v != self.original[k]   # self.original may be None
    }
```

For an object created inside a branch, `original` is `None`. Anything reaching `altered_fields`, `modified_diff` or `original_diff` then raises `TypeError: argument of type 'NoneType' is not a container or iterable`.

Note that the sibling `altered_in_modified` guards correctly, so this looks like an oversight rather than a design choice.

**Suggested fix:** return `set()` when `self.original is None`, matching `altered_in_modified`.

**Workaround here:** `netbox_change_control/diffs.py` renders the diff itself from `original` and `modified` rather than calling those properties.

**Removable when:** the guard is added. We would then use `ChangeDiff.diff` directly.

---

## 3. Plugin models must be exempted by hand, and the failure is silent

**Severity:** medium. Silent data loss for plugin authors.

Any change-logged model belonging to a plugin is branch-tracked by default. A record created while a branch is active is written into that branch's schema and is invisible from main.

For a change-management plugin that is severe: a change request created inside a branch vanishes from main, taking the record of who approved what with it. Nothing warns you; the object simply is not there.

Branching already exempts the commercial `netbox_changes.*` in `constants.EXEMPT_MODELS`, so the problem is understood.

**Suggested fix:** let a plugin declare its own models exempt from its `PluginConfig`, rather than requiring every deployment to add `exempt_models` to the *branching* configuration. A `branching_exempt = True` attribute on the model, or a registry hook, would put the decision with the author who knows the answer.

**Workaround here:** documented as a required installation step. `netbox_change_control` also refuses to treat its own models as protected under `protect_main`.

**Removable when:** exemption can be declared by the plugin that owns the model.

---

## 4. `ChangeDiff.save()` rewrites `object_repr` from the live object

**Severity:** low. Surprising rather than harmful.

Saving a `ChangeDiff` recomputes `object_repr` from the current object, so a stored diff's label changes when the object is renamed, and a historical record no longer reflects what it described at the time.

It also makes tests brittle: a test that creates a diff with a chosen `object_repr` gets a different value back, which cost us time more than once.

**Suggested fix:** set `object_repr` on creation only, or document that it tracks the live object.

**Workaround here:** we store our own label on `ChangeComment.change_label` so a comment survives its diff.

---

## 5. A change on main does not recompute conflicts (FIXED in 1.1.3)

**Status:** fixed upstream by [#625](https://github.com/netboxlabs/netbox-branching/pull/625), released in 1.1.3. Kept here because it is why this plugin requires that version.

On 1.1.2 and earlier, `record_change_diff` updated the affected diffs with a queryset `.update()`, which never calls `save()`, so `ChangeDiff.conflicts` kept the value it had before main moved. Two symptoms:

- edit an object in a branch, then edit the same field on main, and the branch reported no conflict at all. Branching's own merge form gates on that column, so it demanded no acknowledgement and the merge overwrote main silently;
- resolve a conflict by editing main to match the branch, and the flag never cleared.

1.1.3 replaces the bulk write with a loop that saves each diff with `update_fields=('current', 'conflicts', 'last_updated')`, which runs `_update_conflicts()`. The same change stops `save()` recomputing `object_repr` unless that field is being saved, which partly addresses item 4.

Re-tested on 1.1.3:

```
after branch edit : None
after main edit   : ['description']    (1.1.2 left this None)
after main matches: None               (1.1.2 left this flagged)
```

---

## 6. No conflict resolution, only acknowledgement

**Severity:** low as a bug, high as a feature request.

The only resolutions are to force the branch's value over main by acknowledging, or to hand-edit one side until the two agree. There is no field-level "keep mine / keep theirs", and no way to record what was decided.

**Suggested fix:** a per-field resolution on the merge form, recording the choice on the `ChangeDiff` so it appears in the audit trail.

**Workaround here:** none. [`docs/conflicts.md`](../docs/conflicts.md) explains the two manual routes.

---

## Reporting these

Items 1 and 5 were fixed upstream. The rest have not been filed. Before filing, reproduce against the latest netbox-branching, since 1.1.3 is what we tested.

Issues: <https://github.com/netboxlabs/netbox-branching/issues>
