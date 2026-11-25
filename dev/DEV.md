# Development

Everything needed to run, seed, test and photograph `netbox-change-control` locally. The user-facing documentation is in [`docs/`](../docs), and [`AGENTS.md`](../AGENTS.md) maps the architecture.

## The local stack

The stack lives in [`stack/`](stack): PostgreSQL, Redis, NetBox 4.7.6 with netbox-branching, and its worker. It mounts this working copy and puts it on `PYTHONPATH`, with `dev/` for `netbox_change_control_dev`, the development plugin that holds the seed commands. That plugin is not part of the published package, which packages only `netbox_change_control`. [`stack/plugins.py`](stack/plugins.py) is the NetBox configuration every developer shares.

Docker and GNU make are enough. From the repository root:

```bash
make up    # build the image, start the stack, wait until NetBox answers
make seed  # once, on a new database
```

The first start migrates an empty database, which takes several minutes. Then open <http://localhost:8890> and sign in as `admin` / `admin`. A source change needs `make reload`, never a rebuild.

> [!WARNING]
> The stack is for development and tests only, never for production. Every password, key and token in the compose file is a fixed value that anyone can read in this repository.

Its ports listen on `127.0.0.1` only. Set `NETBOX_PORT`, `E2E_PORT` or `WEBHOOK_PORT` to move them, and `BIND_ADDRESS` to listen on another address, for a stack on a remote machine you reach through a private network.

## Make targets

Run `make help` for the full list. The main ones:

| Target | What it does |
|---|---|
| `make up` | Build the image on first use, start the stack, and wait until NetBox answers. |
| `make reload` | Restart NetBox and the worker to load source changes. |
| `make seed` | Build the demo scenario below. |
| `make adopt` | Open change requests for existing branches. |
| `make test` | Run the unit tests inside the container. `ARGS=` picks a module. |
| `make e2e` | Start the e2e NetBox and run the browser tests against it. |
| `make migrations` | Generate migrations, written as your host user rather than the container's. |
| `make migrations-check` | Fail if the models and the migrations disagree, as CI does. |
| `make lint` / `make format` | ruff, pinned to the version CI uses. |
| `make captures` | Build the states the documentation screenshots need. |
| `make webhook` | Start the webhook receiver and follow its log. |
| `make down` / `make destroy` | Stop the stack; `destroy` also deletes its databases. |

> [!NOTE]
> `make migrations` runs as `$(id -u):$(id -g)`. The container runs as uid 999, so without that the generated migration lands root-owned in the bind mount and you cannot edit it. `DEVELOPER=true` is set in the compose file because NetBox refuses to run `makemigrations` without it.

## Test accounts

`make seed` creates two reviewer groups and five users. Every seeded user has the password **`demo1234`**.

| Username | Group | Satisfies rules needing |
|---|---|---|
| `erin` | Change Engineers | an engineer |
| `frank` | Change Engineers | an engineer |
| `grace` | Change Engineers | an engineer |
| `helen` | Change Leads | a lead |
| `ivan` | Change Leads | a lead |

The seed also grants these groups the object permissions they need: read and write on change requests, reviews and comments; read on policies, merge checks and branches; read on devices, circuits and IPAM; and write on circuits, so `protect_main` can be demonstrated.

Without those grants a NetBox user logs in to an empty application, which looks like a broken plugin.

The `admin` account stays a superuser and sees everything.

> [!IMPORTANT]
> Superusers hold every permission, so an admin account is never blocked by `protect_main` and never sees the review form on a request it opened. Use a seeded reviewer to exercise those paths. A private browsing window lets you stay signed in as both at once.

## What the seed creates

Five graded policies, chosen to exercise every matching path:

| Policy | Scope | Requires |
|---|---|---|
| Baseline review | every branch | 1 engineer |
| Device changes | `dcim.device`, `dcim.interface` | 2 engineers **and** 1 lead |
| Circuit changes | `circuits.*` | 1 engineer **and** 1 lead |
| IPAM changes | `ipam.prefix`, `ipam.ipaddress`, `ipam.vlan` | 1 engineer |
| Site and rack changes | `dcim.site`, `dcim.rack`, `dcim.location` | 2 leads (disabled, to show a disabled policy) |

It also provisions a branch, edits a device inside it, and opens a change request against it.

> [!NOTE]
> The seed wraps its branch edit in `netbox.context_managers.event_tracking` with a synthetic request. NetBox writes `ObjectChange` records only inside a request context. Without it the branch records no changes, `ChangeDiff` stays empty, and every scoped policy silently fails to match.

`make adopt` opens a change request for branches that already exist, for example one you created by hand in the interface.

## Walking through a review

`make seed` edits a device inside its branch, so two policies attach and three rules must pass:

| Rule | Needs |
|---|---|
| Baseline review / One engineer | 1 engineer |
| Device changes / Two engineers | 2 engineers |
| Device changes / One lead | 1 lead |

1. Sign in as `admin`. Open **Change Control > Change Requests** and select the request. The Approval status panel lists every rule at 0 of its minimum, and names who may approve each one.

   `admin` opened the seeded requests, so it sees no review form: a user cannot review their own request. The panel says so rather than showing nothing.

2. Sign in as **`erin`**, an engineer. Open the same request, choose **Approve**, submit.

   One rule passes. Baseline review is satisfied, the two-engineer rule stands at 1 of 2, and the lead rule is untouched. This is the behaviour that matters: an approval counts only toward the rules its author is eligible for, so one engineer cannot carry a change that asks for two.

3. Sign in as **`frank`**, the second engineer. Approve.

   Two rules pass. The lead rule is still short, so the merge stays blocked.

4. Sign in as **`helen`**, a lead. Approve.

   All three rules pass and the status becomes **Approved**.

5. Resolve any open comment thread on the **Changes** tab, or the `threads-resolved` check keeps the merge blocked.

6. The **Merge branch** button is now active on the change request.

To run it again, delete the reviews on the request and set its status back to Needs review.

> [!NOTE]
> `make seed` only ever adds users to groups, so a user you moved by hand keeps their old membership. If a lead appears to satisfy an engineer rule, check their groups before suspecting the policy engine.

## Testing

[`AGENTS.md`](../AGENTS.md) holds the test strategy: browser journeys first, a unit test only for what a journey cannot reach cheaply.

```bash
make test   # the unit tests, inside the container, against the real NetBox and branching code
make e2e    # the browser tests in e2e/
```

`make e2e` runs the journeys against a second NetBox, `netbox-e2e` on <http://localhost:8891>, with its own database (`netbox_e2e`), so the demo policies of the seed cannot change what they see. It recreates that NetBox on each run to load the current source, installs the Playwright browser, and runs `pytest e2e`. `ARGS=-k window` runs some of them, `ARGS=--headed` shows the browser. `make e2e-down` stops it.

`tests/test_state_machine.py` reads the transition table of `docs/change-requests.md`, so a change to the lifecycle starts with that table.

Most unit test classes extend `ChangeControlTestCase` from `tests/base.py`, which gives each test a branch, a change request and a policy requiring one review. Set `approved = True` for the review to be given already, and `branch_prefix` to name the branch.

Fixture classes must declare no tests of their own. Subclassing a class which has tests re-runs every one of them under the subclass, which inflates the count without covering anything new.

Unit tests construct branches without provisioning a PostgreSQL schema. Two consequences worth knowing:

- `Branch.get_unmerged_changes()` returns nothing, so nothing is ever stale by default. Tests that exercise staleness drive the branch timestamp through a patched clock.
- A plain `ChangeDiff.save()` rewrites `object_repr` from the live object, so do not assert on a repr the test set itself. Since branching 1.1.3 it is left alone when `update_fields` excludes it.

## Testing webhooks

`make webhook` starts a receiver in the stack and follows its log. In NetBox, create a webhook with the URL `http://webhook:9000/hook`, and an event rule that sends the change request events to it. Each delivery prints in full, <http://localhost:8892/> lists them newest first, and a `POST` to `/reset` clears the list.

Docker passes the host's HTTP proxy into the containers, and NetBox sends webhooks through it. The proxy router in `stack/plugins.py` sends a webhook directly when its host is a compose service or a private address.

## Enabling `protect_main` locally

`stack/plugins.py` enables it scoped to circuits:

```python
'protect_main': True,
'protect_main_scope': ['circuits.*'],
```

Grant `erin` the bypass by creating a permission on **Change Control > Policy** with the additional action `bypass`; without it only superusers are exempt.

Sign in as `erin` and edit a circuit provider on main: refused. Edit a site on main: allowed, because it is outside the scope. Make the same circuit edit inside a branch: allowed.

## Taking the screenshots

The pages in `docs/` reference images from `docs/img/`. Each shot needs the application in one particular state, which is tedious to reach by hand and easy to get subtly wrong. Build them all instead:

```bash
make seed        # once, for the reviewers and policies
make captures    # the states, plus a list of what to shoot and where
```

It prints a URL, a user and a description for every outstanding shot. Three change requests are built:

| Branch | State | Serves |
|---|---|---|
| `capture-ready` | Approved by two engineers and a lead, every check passing. | The active merge button. |
| `capture-blocked` | Approved by a lead only, one comment thread still open. | The checks panel with a failure, and Erin's My Reviews widget. |
| `capture-conflict` | Main has moved the same field the branch changed. | The conflict banner and a blocked merge. |

It also places the My Reviews widget on Erin's dashboard, so that shot does not depend on adding it by hand first.

Run it as often as you like: it tears its own branches down first, so every session starts from the same picture. `make captures-clean` removes them and puts the device it changed on main back.

> [!NOTE]
> The conflict scenario resets its device on main before taking the branch. Without that, a second run would branch from the value the first run left behind, make the same edit again, record no change, and quietly produce no conflict.

Capture at roughly 1400px wide in light mode, cropped to the card rather than the whole window, except for `change-request-detail.png` and `my-reviews-widget.png` which are meant to show context.

## Code review

Greptile reviews each pull request when it opens and after each push. Its configuration is in `.greptile/`: `config.json` sets what it comments on, and `files.json` gives it `AGENTS.md` and the design pages as context. Comment `@greptileai` on a pull request to ask for a new review.

## Releasing

1. In `CHANGELOG.md`, rename the `Unreleased` heading to the new version and its date.
2. Bump `version` in `pyproject.toml` and `__version__` in `netbox_change_control/__init__.py`, then run `uv lock` so `uv.lock` records it.
3. Add the release to `COMPATIBILITY.md`.
4. Tag and build.
