"""
Query counts on the pages which grow.

A list view is safe on its own: NetBox's `NetBoxTable` derives `prefetch_related` from the
columns it is about to render, in `configure()`, which `ObjectListView` calls for it. A table
built by hand in `get_extra_context()` never has `configure()` called, so it gets none of that
and every linkified column costs one query per row.

These tests do not pin a number, because the baseline moves whenever NetBox adds a query of
its own and a pinned count would then fail for a reason that has nothing to do with the
plugin. They pin the shape instead: doubling the rows must cost nothing.

Both measurements are taken on a page which already has rows. Comparing against an empty or
near-empty page measures the empty state as well, which legitimately differs by a query or two
in either direction, and that is noise rather than the thing under test.
"""

from core.choices import ObjectChangeActionChoices
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from netbox_branching.models import ChangeDiff
from users.models import Group, User

from netbox_change_control.choices import ReviewDecisionChoices
from netbox_change_control.models import ChangeComment, Policy, PolicyRule, Review
from netbox_change_control.tests.base import ChangeControlTestCase

ROWS = 20


class QueryCountTest(ChangeControlTestCase):
    policy_checks = ()

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user(username='viewer', is_superuser=True))

    def queries_for(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx), response.content.count(b'<tr')

    def assert_flat(self, url, add_rows):
        """Fill the page, measure, double the rows, measure again."""
        add_rows('a')
        # Render once and throw the count away. Django caches content types for the life of
        # the process, so the first render of any page pays for lookups the second does not,
        # and that one-off would otherwise read as a difference between the two measurements.
        self.queries_for(url)
        before, rows_before = self.queries_for(url)
        add_rows('b')
        after, rows_after = self.queries_for(url)

        self.assertGreater(rows_after, rows_before, f'{url} rendered no extra rows, so this proves nothing')
        self.assertEqual(
            after,
            before,
            f'{url} issued {after - before} more queries for {rows_after - rows_before} more rows',
        )

    def test_a_policy_page_costs_the_same_whatever_its_rules(self):
        def add_rows(batch):
            group = Group.objects.create(name=f'Reviewers {batch}')
            for i in range(ROWS):
                rule = PolicyRule.objects.create(policy=self.policy, name=f'Rule {batch}{i}')
                rule.groups.set([group])
                rule.users.set([self.reviewer])

        self.assert_flat(self.policy.get_absolute_url(), add_rows)

    def test_the_changes_tab_costs_the_same_whatever_its_comments(self):
        url = reverse('plugins:netbox_change_control:changerequest_changes', args=[self.cr.pk])

        def add_rows(batch):
            for i in range(ROWS):
                diff = ChangeDiff.objects.create(
                    branch=self.branch,
                    object_type=ContentType.objects.get_for_model(Policy),
                    object_id=self.policy.pk,
                    object_repr=f'object-{batch}{i}',
                    action=ObjectChangeActionChoices.ACTION_UPDATE,
                    last_updated=None,
                )
                ChangeComment.objects.create(
                    change_request=self.cr, change_diff=diff, author=self.requester, text=f'note {batch}{i}'
                )

        self.assert_flat(url, add_rows)

    def test_the_reviews_tab_costs_the_same_whatever_its_reviews(self):
        url = reverse('plugins:netbox_change_control:changerequest_reviews', args=[self.cr.pk])

        def add_rows(batch):
            for i in range(ROWS):
                Review.objects.create(
                    change_request=self.cr,
                    reviewer=User.objects.create(username=f'reviewer-{batch}{i}'),
                    decision=ReviewDecisionChoices.APPROVE,
                )

        self.assert_flat(url, add_rows)
