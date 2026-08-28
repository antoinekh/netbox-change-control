# Extending this plugin

Another plugin can add content to this plugin's pages, and checks to its merge gate. Nothing special is required of you.

## Injecting content into a page

Every model here supports NetBox's standard `PluginTemplateExtension` hooks. This is a NetBox feature, and the [NetBox documentation](https://netboxlabs.com/docs/netbox/en/stable/plugins/development/views/#extra-template-content) is the reference for it.

All six hooks (`buttons`, `alerts`, `left_page`, `right_page`, `full_width_page` and `list_buttons`) work on `policy`, `policyrule`, `changerequest`, `review` and `mergecheck`.

## Adding a pre-merge check

The most useful extension point is a check that gates the merge. See [Writing your own checks](custom-checks.md).

## What this plugin guarantees

The test suite renders every page with a probe extension registered and fails if any hook is missing, so a template change here cannot silently close a page to extension. See `tests/test_extensibility.py`.
