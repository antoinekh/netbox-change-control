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

**It sees the state the branch proposes**, not the state of main. For an edit or a creation that is the object as the branch leaves it; for a deletion it is the object being removed.

**Related objects appear as numeric ids**, because the diff stores them that way. So a site is `{"attr": "site", "value": 5}`, not the site's name. Use the object type scope for anything you would otherwise express as a name, and keep conditions for plain values such as status, a boolean flag, or a string.

> [!TIP]
> A condition naming a field the object does not have simply does not match; it does not error. That means a typo in `attr` silently produces a policy that never applies. Check a new condition against a real branch before relying on it.

> [!NOTE]
> Conditions cost a scan of the branch diff per condition-bearing policy. Policies scoped only by object type cost nothing extra, so prefer the object type scope where it is sufficient.
