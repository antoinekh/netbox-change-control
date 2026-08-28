# Writing your own checks

A check is a function that takes a `ChangeRequest` and returns a `CheckResult`. Register it once at startup.

`CheckResult` has three constructors:

| Constructor | Result |
|---|---|
| `CheckResult.passed(summary='')` | `success` |
| `CheckResult.failed(summary)` | `failure` |
| `CheckResult.skipped(summary='')` | `skipped` |

Each accepts a `summary` shown next to the check. For a link to a build log or report, construct it directly: `CheckResult('success', 'All good', 'https://ci/build/99')`.

Put the function in your own plugin, then register it from that plugin's `ready()`:

```python
# my_plugin/checks.py
from netbox_change_control.checks import CheckResult

def no_production_devices(change_request):
    """
    Refuse a merge which touches a device in a production site.
    """
    from dcim.models import Device
    from netbox_branching.models import ChangeDiff

    diffs = ChangeDiff.objects.filter(
        branch=change_request.branch,
        object_type__app_label='dcim',
        object_type__model='device',
    )
    if not diffs.exists():
        return CheckResult.skipped('No devices touched.')

    device_ids = diffs.values_list('object_id', flat=True)
    offenders = Device.objects.filter(pk__in=device_ids, site__status='active')
    if offenders.exists():
        names = ', '.join(d.name for d in offenders[:3])
        return CheckResult.failed(f'{offenders.count()} production device(s): {names}')

    return CheckResult.passed(f'{diffs.count()} device(s), none in production.')
```

```python
# my_plugin/__init__.py
from netbox.plugins import PluginConfig

class MyPluginConfig(PluginConfig):
    name = 'my_plugin'
    # ...

    def ready(self):
        super().ready()

        from netbox_change_control.checks import register_check

        from .checks import no_production_devices

        register_check(
            'no-production-devices',   # stable identifier, used as the database key
            'No production devices',   # label shown in the interface
            no_production_devices,
            required=True,             # False makes it advisory
        )
```

`scope` decides where the check applies. The default, `CheckScope.ALWAYS`, puts it on every change request. Pass `scope=CheckScope.POLICY` to make it opt-in, so it appears in the **Registered checks** list on the policy form and runs only where a policy asks for it. The built-in checks all use `POLICY`. See [which checks apply, and where](checks.md#which-checks-apply-and-where).

The check now runs whenever checks are refreshed: when the branch is synced or reverted, when a comment thread changes, and when someone presses **Re-run checks**.

> [!TIP]
> A check that raises is recorded as `error` with the exception text as its summary. A broken check blocks the merge but never breaks the page.

> [!IMPORTANT]
> `name` is the database key. Renaming it creates a new check and deletes the old one, discarding its history. Change the `label` freely; leave the `name` alone.

## Example: an AI reviewer

A check can ask a model to read the diff and flag risky changes. This one summarises the branch and asks Claude for a verdict.

Install the SDK into the NetBox environment (`pip install anthropic`) and set `ANTHROPIC_API_KEY`.

```python
# my_plugin/checks.py
import anthropic

from netbox_change_control.checks import CheckResult

PROMPT = """You are reviewing a proposed change to a network source of truth.

Below is every object the change touches, with its old and new values.

Answer with exactly one line, in this format:
VERDICT: PASS|FAIL
REASON: <one sentence, at most 150 characters>

Answer FAIL only for a change that is likely to cause an outage or that
contradicts itself. Cosmetic changes, descriptions and tags are PASS.

CHANGES:
{changes}
"""

def _summarise(change_request):
    """
    Render the branch diff as plain text for the model.
    """
    from netbox_change_control.diffs import build_change_rows
    from netbox_branching.models import ChangeDiff

    diffs = list(ChangeDiff.objects.filter(branch=change_request.branch)
                 .select_related('object_type'))
    rows = build_change_rows(diffs)

    lines = []
    for diff in diffs:
        lines.append(f'{diff.get_action_display()} {diff.object_type.name}: {diff.object_repr}')
        for attr in rows[diff.pk]:
            lines.append(
                f'  {attr["field"]}: {attr["original_display"]} -> {attr["modified_display"]}'
            )
    return '\n'.join(lines), len(diffs)

def ai_review(change_request):
    """
    Ask Claude to review the branch diff for risk.
    """
    changes, count = _summarise(change_request)
    if not count:
        return CheckResult.skipped('Nothing to review.')

    client = anthropic.Anthropic()
    response = client.messages.create(
        model='claude-opus-5',
        max_tokens=1024,
        thinking={'type': 'adaptive'},
        output_config={'effort': 'low'},
        system='You are a careful network change reviewer. Be concise and specific.',
        messages=[{'role': 'user', 'content': PROMPT.format(changes=changes)}],
    )

    text = '\n'.join(b.text for b in response.content if b.type == 'text')
    verdict = 'FAIL' if 'VERDICT: FAIL' in text.upper() else 'PASS'
    reason = next(
        (line.split(':', 1)[1].strip() for line in text.splitlines()
         if line.upper().startswith('REASON:')),
        'No reason given.',
    )

    if verdict == 'FAIL':
        return CheckResult.failed(reason[:500])
    return CheckResult.passed(reason[:500])
```

Register it as **advisory**:

```python
register_check('ai-review', 'AI review', ai_review, required=False)
```

> [!WARNING]
> Every check runs on **every** refresh, including each time a comment thread changes. An API call there costs money and adds latency to saving a comment.

The whole diff goes to a third party. The model sees only what `_summarise` sends: it has no view of the wider network, so it cannot reason about anything outside the diff.

## Checks reported by an external system

For work that happens outside NetBox, such as a CI pipeline, declare the check by name. It is created as pending and blocks the merge until something reports a result.

```python
PLUGINS_CONFIG = {
    'netbox_change_control': {
        'required_external_checks': [
            ('ci-pipeline', 'CI pipeline'),
            'lint',                       # a bare string uses the name as the label
        ],
    },
}
```

Your pipeline finds the check and reports on it:

```bash
# Find the check for this change request
CHECK_ID=$(curl -s -H "Authorization: Token $TOKEN" \
  "$NETBOX/api/plugins/change-control/checks/?change_request_id=$CR_ID&name=ci-pipeline" \
  | jq -r '.results[0].id')

# Report the result
curl -X PATCH "$NETBOX/api/plugins/change-control/checks/$CHECK_ID/" \
  -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "status": "success",
        "summary": "42 tests passed",
        "details_url": "https://ci.example.com/build/99"
      }'
```

Set `status` to `running` when the job starts, so reviewers can see it is in progress.

The API deliberately does not let a reporter decide whether its own check counts. Report `status`, `summary` and `details_url`; the `required` flag is read from your configuration.
