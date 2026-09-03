# Extending this plugin

Another plugin can add content to this plugin's pages, and checks to its merge gate. Nothing special is required of you.

## What this plugin injects elsewhere

The traffic runs both ways. This plugin uses the same hooks to put its own content on
netbox-branching's branch page: the change request governing that branch, its status, what is
still outstanding and a link to it, or an offer to open one when there is none. It uses the
`alerts` and `right_page` hooks, and `branch_page_placement` decides which of them renders, so
a deployment can compare the two or turn both off. See `netbox_change_control/template_content.py`.

## Injecting content into a page

Every model here supports NetBox's standard `PluginTemplateExtension` hooks. This is a NetBox feature, and the [NetBox documentation](https://netboxlabs.com/docs/netbox/en/stable/plugins/development/views/#extra-template-content) is the reference for it.

All six hooks (`buttons`, `alerts`, `left_page`, `right_page`, `full_width_page` and `list_buttons`) work on `policy`, `policyrule`, `changerequest`, `review` and `mergecheck`.

## Adding a pre-merge check

The most useful extension point is a check that gates the merge. See [Writing your own checks](custom-checks.md).

Three ways in, in order of how much work they are:

| Way in | Written by | Use it for |
|---|---|---|
| [An event rule](event-rules.md#reporting-a-check-without-leaving-netbox) | Anybody who can add one | A condition on the objects in the branch |
| [The REST API](custom-checks.md#checks-reported-by-an-external-system) | A pipeline | Work that happens outside NetBox |
| [A registered function](custom-checks.md#writing-and-registering-one) | A plugin | A question that needs Python, in-process |

## What this plugin guarantees

The test suite renders every page with a probe extension registered and fails if any hook is missing, so a template change here cannot silently close a page to extension. See `tests/test_extensibility.py`.
