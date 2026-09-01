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

Available operators are `eq` (the default), `gt`, `gte`, `lt`, `lte`, `in`, `contains` and `regex`. Add `"negate": true` to invert any condition.

| Goal | Condition |
|---|---|
| Only objects that are live | `{"attr": "status", "value": "active"}` |
| Anything except decommissioning | `{"attr": "status", "value": "decommissioning", "negate": true}` |
| One of several statuses | `{"or": [{"attr": "status", "value": "active"}, {"attr": "status", "value": "planned"}]}` |
| A circuit ID matching a pattern | `{"attr": "cid", "value": "^DEOW", "op": "regex"}` |
| A partial match | `{"attr": "cid", "value": "100", "op": "contains"}` |
| One of a set of ids | `{"attr": "type", "value": [1, 2], "op": "in"}` |
| Live **and** has a tenant | `{"and": [{"attr": "status", "value": "active"}, {"attr": "tenant", "value": null, "negate": true}]}` |

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
