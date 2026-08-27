"""
Markdown in comments.

Every surface that shows user-written text must render it. A table renders through a column,
not through the page template, so fixing the templates alone left the Reviews tab showing raw
`**bold**`.
"""

from django.test import TestCase
from django.urls import reverse
from netbox_branching.models import Branch
from users.models import User

from netbox_change_control.choices import ReviewDecisionChoices
from netbox_change_control.models import ChangeComment, ChangeRequest, Review

SOURCE = '**bold text**\n\nsecond paragraph with `code`\n\n- one\n- two'


class MarkdownRenderingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='viewer', is_superuser=True)
        cls.requester = User.objects.create(username='requester')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.branch = Branch.objects.create(name='markdown')
        cls.cr = ChangeRequest.objects.create(branch=cls.branch, title='T', requester=cls.requester)
        cls.review = Review.objects.create(
            change_request=cls.cr,
            reviewer=cls.reviewer,
            decision=ReviewDecisionChoices.APPROVE,
            comment=SOURCE,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _assert_rendered(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        content = response.content.decode()
        self.assertIn('<strong>bold text</strong>', content, f'{url} did not render Markdown')
        self.assertIn('<code>code</code>', content)
        self.assertIn('<li>one</li>', content)
        # The raw source must not leak through alongside the rendered form.
        self.assertNotIn('**bold text**', content)

    def test_the_change_request_page_renders_a_review_comment(self):
        self._assert_rendered(self.cr.get_absolute_url())

    def test_the_reviews_tab_renders_a_review_comment(self):
        """
        The tab renders through ReviewTable, so it needs a Markdown column of its own.
        """
        self._assert_rendered(reverse('plugins:netbox_change_control:changerequest_reviews', args=[self.cr.pk]))

    def test_the_review_list_renders_a_comment(self):
        self._assert_rendered(reverse('plugins:netbox_change_control:review_list'))

    def test_the_review_detail_page_renders_a_comment(self):
        self._assert_rendered(reverse('plugins:netbox_change_control:review', args=[self.review.pk]))


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


class ChangeCommentMarkdownTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='viewer2', is_superuser=True)
        cls.requester = User.objects.create(username='requester2')
        cls.branch = Branch.objects.create(name='markdown-comments')
        cls.cr = ChangeRequest.objects.create(branch=cls.branch, title='T', requester=cls.requester)

    def setUp(self):
        self.client.force_login(self.user)

    def test_a_change_comment_renders_on_the_changes_tab(self):

        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.models import Policy

        policy = Policy.objects.create(name='anchor')
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=policy.pk,
            object_repr='obj',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )
        ChangeComment.objects.create(change_request=self.cr, change_diff=diff, author=self.user, text=SOURCE)

        response = self.client.get(reverse('plugins:netbox_change_control:changerequest_changes', args=[self.cr.pk]))
        content = response.content.decode()
        self.assertIn('<strong>bold text</strong>', content)
        self.assertNotIn('**bold text**', content)
