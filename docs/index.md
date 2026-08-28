# NetBox Change Control documentation

Policy-driven change control and mandatory review for NetBox branches.

New here? Read [Installation and configuration](installation.md), then [Policies and rules](policies.md). Everything else can wait until you need it.

## Setting up

| Page | Covers |
|---|---|
| [Installation and configuration](installation.md) | Requirements, installing the plugin, and every configuration setting. |
| [Permissions](permissions.md) | The permissions each role needs, and how to grant the two exemptions. |

## Day to day

| Page | Covers |
|---|---|
| [Policies and rules](policies.md) | Scoping a policy, writing rules, and how policies attach on their own. |
| [Policy conditions](policy-conditions.md) | Narrowing a policy on the values of the objects themselves, with worked examples. |
| [Conflicts with main](conflicts.md) | What counts as a real conflict, and how to resolve one. |
| [Change requests](change-requests.md) | The request lifecycle, why approved is not the same as mergeable, and what survives a branch deletion. |
| [Reviews](reviews.md) | Submitting a review, and where the form lives. |
| [Reviewing individual changes](reviews.md#reviewing-individual-changes) | The Changes tab, per-object comment threads and replies. |

## Gating a merge

| Page | Covers |
|---|---|
| [Pre-merge checks](checks.md) | What a check is, the built-in ones, and choosing which policies apply them. |
| [Writing your own checks](custom-checks.md) | The registry, a worked example, an AI reviewer, and reporting from CI. |
| [Event rules](event-rules.md) | Firing a webhook or a script on a change request. |
| [Merging, windows and auto-merge](merging.md) | Where the merge button is, change windows, and merging automatically. |
| [Protecting main](protect-main.md) | Requiring a branch, and limiting that to part of NetBox. |

## Reference

| Page | Covers |
|---|---|
| [Automatic behaviours and notifications](automation.md) | Stale reviews, policy reevaluation, notifications, the dashboard widget. |
| [REST API](api.md) | Every endpoint. |
| [Extending this plugin](extending.md) | Injecting content into these pages, and adding checks. |
| [Design](design.md) | Why the plugin is built the way it is. |
