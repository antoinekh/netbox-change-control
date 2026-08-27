# Extending this plugin

Another plugin can add content to this plugin's pages, tabs to its models, and checks to its merge gate. Nothing special is required of you.

## Injecting content into a page

Every model here supports NetBox's standard `PluginTemplateExtension` hooks. This is a NetBox feature, and the [NetBox documentation](https://netboxlabs.com/docs/netbox/en/stable/plugins/development/views/#extra-template-content) is the reference for it.

All six hooks (`buttons`, `alerts`, `left_page`, `right_page`, `full_width_page` and `list_buttons`) work on `policy`, `policyrule`, `changerequest`, `review` and `mergecheck`.

> [!NOTE]
> Page content appears on an object's own page, not on its tabs. This matches core NetBox, where the Changes and Reviews tabs render their own templates.

## Adding a tab

`register_model_view` attaches a tab to any of this plugin's models:

```python
from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from netbox_change_control.models import ChangeRequest


@register_model_view(ChangeRequest, 'tickets', path='tickets')
class ChangeRequestTicketsView(generic.ObjectView):
    queryset = ChangeRequest.objects.all()
    template_name = 'my_plugin/changerequest_tickets.html'
    tab = ViewTab(label='Tickets', badge=lambda obj: 1)
```

## Adding a pre-merge check

The most useful extension point is a check that gates the merge. See [Writing your own checks](custom-checks.md).

## What this plugin guarantees

The test suite renders every page with a probe extension registered and fails if any hook is missing, so a template change here cannot silently close a page to extension. See `tests/test_extensibility.py`.
