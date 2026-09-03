# Policy conditions

Object types are usually enough: "changes to circuits need a lead". A **condition set** narrows further, on the values of the objects themselves: "changes to *active* circuits need a lead".

Conditions use NetBox's own condition set syntax, the same one event rules use. A single condition is a dictionary:

```json
{"attr": "status", "value": "active"}
```

Combine several with `and` or `or`:

```json
{
  "and": [
    {"attr": "status", "value": "active"},
    {"attr": "tenant", "value": null, "negate": true}
  ]
}
```

Available operators are `eq` (the default), `gt`, `gte`, `lt`, `lte`, `in`, `contains` and `regex`. Add `"negate": true` to invert any condition. Two more, `changed` and `unchanged`, compare the two sides of the change instead of comparing one side against a value; see [reading the transition itself](#reading-the-transition-itself).

| Goal | Condition |
|---|---|
| Only objects that are live | `{"attr": "status", "value": "active"}` |
| Anything except decommissioning | `{"attr": "status", "value": "decommissioning", "negate": true}` |
| One of several statuses | `{"or": [{"attr": "status", "value": "active"}, {"attr": "status", "value": "planned"}]}` |
| A circuit ID matching a pattern | `{"attr": "cid", "value": "^DEOW", "op": "regex"}` |
| A partial match | `{"attr": "cid", "value": "100", "op": "contains"}` |
| One of a set of ids | `{"attr": "type", "value": [1, 2], "op": "in"}` |
| Live **and** has a tenant | `{"and": [{"attr": "status", "value": "active"}, {"attr": "tenant", "value": null, "negate": true}]}` |
| The status changed, whatever to whatever | `{"attr": "status", "op": "changed"}` |
| A circuit being switched off | `{"and": [{"attr": "status", "op": "changed"}, {"attr": "snapshots.prechange.status", "value": "active"}]}` |

A worked example. This policy demands a lead only when the branch touches a circuit that is actually in service, so provisioning a planned circuit stays a one-engineer change:

| Field | Value |
|---|---|
| Object types | `circuits.circuit` |
| Conditions | `{"attr": "status", "value": "active"}` |

Three things decide whether the condition matches.

**It is evaluated per changed object, and any one match attaches the policy.** A branch touching ten circuits attaches this policy if a single one of them is active.

**It reads both sides of the change**, and never the state of main. See [which side a condition reads](#which-side-a-condition-reads) below, which is the part worth understanding before you write a condition on `status`.

**Related objects appear as numeric ids**, because the diff stores them that way. So a site is `{"attr": "site", "value": 5}`, not the site's name. Use the object type scope for anything you would otherwise express as a name, and keep conditions for plain values such as status, a boolean flag, or a string.

!!! tip

    A condition naming a field the object does not have simply does not match; it does not error. That means a typo in `attr` silently produces a policy that never applies.

!!! note

    Conditions cost a scan of the branch diff per condition-bearing policy, evaluated in Python, one condition set per changed object. A policy that matches stops at the first object that satisfies it; one that does not match reads them all. On a branch touching thousands of objects with several conditional policies, this is the slowest thing the plugin does. Policies scoped only by object type cost nothing extra.

## Which side a condition reads

A change has two sides: the object **before** it and the object **after** it. Which one the condition reads decides what the policy actually protects, and the two readings differ in a way that matters.

Take `{"attr": "status", "value": "active"}` on circuits. Read only against the state the branch leaves, it does not mean "active circuits". It means **"changes that leave the circuit active"**. Decommissioning a live circuit would not match, and that is the change most likely to cause an outage.

The **Condition state** field on the policy decides which side is read:

| Setting | Reads | Use it for |
|---|---|---|
| **Either side of the change** (default) | Both | Protecting what is live: catches a circuit being switched on *and* one being switched off. |
| **After the change** | The object as the branch leaves it | Reviewing anything being promoted into a state, without catching things leaving it. |
| **Before the change** | The object as it stands now | Reviewing anything leaving a state, without catching things entering it. |

The default is **either**, because a policy exists to catch changes. When a condition is ambiguous the safe outcome is to attach the policy and ask for a review, not to skip it silently.

A creation has no before, and a deletion has no after, so each of those is matched on the one side it has whatever the setting.

### Main is never read directly

The diff also holds `current`, the object as it stands in main right now. Conditions never read it. A colleague editing that same object on main cannot, on their own, pull your change request into a policy or push it out of one. Which policies govern a change depends on the change.

A sync is different, and deliberately so. Syncing makes main's values your branch's values, and branching writes them into `modified`. A condition then reads them like anything else in the branch. A branch which touches only a device's description will still see `modified` carry `status: offline` once it syncs that change from main, so a policy conditioned on `status == active` and read **after** stops matching. Read against **either** side it keeps matching, because `original` still holds `active`.

## Reading the transition itself

Everything above compares one side of the change against a value. Sometimes the question is not what a value is, but whether it moved. "Any change to a circuit's status" cannot be written as a comparison at all, and "a circuit being switched off" written as two separate conditions on two separate sides would match a circuit that was already off.

Two operators answer that directly. Neither takes a `value`:

| Operator | True when |
|---|---|
| `changed` | The attribute reads differently before and after the change. |
| `unchanged` | The attribute reads the same on both sides. |

```json
{"attr": "status", "op": "changed"}
```

`attr` is the plain field name. It is resolved inside each side, so write `status`, never `snapshots.prechange.status`, with these two operators.

To pin down one direction, name the side you care about with a `snapshots.` path and combine it with `changed`. This is the condition for a circuit being taken out of service, and it does not fire on a circuit that was already out of service:

```json
{
  "and": [
    {"attr": "status", "op": "changed"},
    {"attr": "snapshots.prechange.status", "value": "active"}
  ]
}
```

The two paths are `snapshots.prechange.<attr>` for the object before the change and `snapshots.postchange.<attr>` for the object after it. They work with every ordinary operator, so `{"attr": "snapshots.postchange.status", "value": "decommissioning"}` reads the same as an ordinary `status` condition pinned to one side.

Three things to know before you use them.

**A choice field carries its raw value.** The two sides store what the database stores, so it is `active`, never `{"value": "active"}`. Write `snapshots.prechange.status`, not `snapshots.prechange.status.value`.

**The Condition state field does not apply.** These conditions read both sides themselves, so **Either**, **After** and **Before** all give the same answer. Condition state governs the plain attribute names only.

**A creation and a deletion each have one side.** A creation has no before and a deletion has no after, exactly as in an event snapshot, so the missing side reads as absent. `changed` is therefore true for both: creating an object does change the attribute, from nothing to something. Add a `snapshots.` condition if you mean a transition between two real values.

!!! note "Requires NetBox 4.7"

    `changed`, `unchanged` and the `snapshots.` paths are NetBox 4.7 features, so they are available from plugin version 0.5.0 onwards. On the 0.4.x line a condition using one of them matches nothing, silently, the same way a typo in `attr` does.
