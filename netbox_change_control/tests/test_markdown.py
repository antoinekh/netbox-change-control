"""
Markdown in comments.

Every surface that shows user-written text must render it. A table renders through a column,
not through the page template, so fixing the templates alone left the Reviews tab showing raw
`**bold**`.
"""

from django.test import TestCase

SOURCE = '**bold text**\n\nsecond paragraph with `code`\n\n- one\n- two'


class MarkdownSanitisingTest(TestCase):
    """
    Rendered Markdown is user input, so it must be sanitised. NetBox does this; the point of
    the test is that we go through NetBox's filter rather than marking text safe ourselves.
    """

    def test_dangerous_input_is_neutralised(self):
        from utilities.templatetags.builtins.filters import render_markdown

        self.assertNotIn('<script', render_markdown('<script>alert(1)</script>'))
        self.assertNotIn('onerror', render_markdown('<img src=x onerror=alert(1)>'))
        self.assertNotIn('javascript:', render_markdown('[x](javascript:alert(1))'))

    def test_ordinary_formatting_survives(self):
        from utilities.templatetags.builtins.filters import render_markdown

        self.assertIn('<strong>ok</strong>', render_markdown('**ok**'))
