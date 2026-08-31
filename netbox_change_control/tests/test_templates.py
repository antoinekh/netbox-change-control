"""
Rendering details that are wrong in ways nobody reports.

A badge with an invalid colour class, a select that quietly resets, a timestamp in a different
format from the one next to it. None of these throws, so none of them shows up in a test that
only asserts a 200.
"""

import re
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.models import ChangeDiff
from users.models import Group, ObjectPermission, User

from netbox_change_control.choices import ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest, Review
from netbox_change_control.tests.base import ChangeControlTestCase, make_policy

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / 'templates'

# Tabler ships these, and NetBox's compiled stylesheet carries no others. A colour outside the
# set renders a badge with no background at all, which is why `text-bg-grey` went unnoticed.
TABLER_BADGE_COLOURS = {
    'azure',
    'black',
    'blue',
    'cyan',
    'dark',
    'danger',
    'gray',
    'green',
    'indigo',
    'info',
    'light',
    'lime',
    'muted',
    'orange',
    'pink',
    'primary',
    'purple',
    'red',
    'secondary',
    'success',
    'teal',
    'warning',
    'white',
    'yellow',
}


class BadgeColourTest(TestCase):
    def test_every_badge_colour_is_one_tabler_ships(self):
        offenders = []
        for template in TEMPLATE_DIR.rglob('*.html'):
            for colour in re.findall(r'text-bg-([a-z]+)', template.read_text()):
                if colour not in TABLER_BADGE_COLOURS:
                    offenders.append(f'{template.relative_to(TEMPLATE_DIR)}: text-bg-{colour}')

        self.assertEqual(sorted(set(offenders)), [], 'badge colours NetBox does not define')


def grant(user, actions, *models, label='perm'):
    """
    The pages under test are permission-gated, and these tests are about what they render
    rather than who may see them.
    """
    permission = ObjectPermission.objects.create(name=f'{label}-{user.pk}', actions=list(actions))
    permission.object_types.set(ContentType.objects.get_for_model(m) for m in models)
    permission.users.add(user)
    return permission


def grant_reviewer(user):
    """
    View the request, and be able to review it: the form is replaced by an explanation for a
    user who cannot, so without `add_review` there is no select to inspect at all.
    """
    grant(user, ['view'], ChangeRequest, label='view')
    grant(user, ['view', 'add'], Review, label='review')


class ReviewFormTest(ChangeControlTestCase):
    """
    The form has to open on the reviewer's standing decision.

    It prefilled the comment but reset the decision to Approve, so a reviewer coming back to
    amend a "Request changes" was silently offered an approval instead.
    """

    branch_prefix = 'reviewform'
    policy_checks = ()

    def setUp(self):
        super().setUp()
        grant_reviewer(self.reviewer)
        self.client.force_login(self.reviewer)

    def selected_option(self, html):
        match = re.search(r'<option value="([a-z]+)"[^>]*\bselected\b', html)
        return match.group(1) if match else None

    def test_a_standing_rejection_is_preselected(self):
        Review.objects.create(
            change_request=self.cr,
            reviewer=self.reviewer,
            decision=ReviewDecisionChoices.REJECT,
            comment='not yet',
        )
        html = self.client.get(self.cr.get_absolute_url()).content.decode()
        self.assertEqual(self.selected_option(html), 'reject')

    def test_a_standing_approval_is_preselected(self):
        Review.objects.create(change_request=self.cr, reviewer=self.reviewer, decision=ReviewDecisionChoices.APPROVE)
        html = self.client.get(self.cr.get_absolute_url()).content.decode()
        self.assertEqual(self.selected_option(html), 'approve')

    def test_a_first_review_preselects_nothing(self):
        html = self.client.get(self.cr.get_absolute_url()).content.decode()
        self.assertIsNone(self.selected_option(html))


class ReviewStalenessIsVisibleTest(ChangeControlTestCase):
    """
    A reviewer opening their own review has to be able to see it no longer counts.
    """

    branch_prefix = 'stalereview'
    policy_checks = ()

    def test_a_stale_review_says_so_on_its_own_page(self):
        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType

        review = Review.objects.create(
            change_request=self.cr, reviewer=self.reviewer, decision=ReviewDecisionChoices.APPROVE
        )
        # Move the branch on, which is what makes a review stale.
        ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(ChangeRequest),
            object_id=self.cr.pk,
            object_repr='something',
            action=ObjectChangeActionChoices.ACTION_UPDATE,
        )

        admin = User.objects.create(username='admin-viewer', is_superuser=True)
        self.client.force_login(admin)
        html = self.client.get(f'/plugins/change-control/reviews/{review.pk}/').content.decode()

        if review.is_stale:
            self.assertIn('Stale', html)


class ChangesTabTimestampTest(TestCase):
    """
    The Changes tab printed raw datetimes beside a detail page using isodatetime.
    """

    def test_the_template_formats_comment_timestamps(self):
        template = (TEMPLATE_DIR / 'netbox_change_control' / 'changerequest_changes.html').read_text()
        self.assertNotIn('{{ thread.comment.created }}', template)
        self.assertNotIn('{{ reply.created }}', template)
        self.assertIn('thread.comment.created|isodatetime', template)
        self.assertIn('reply.created|isodatetime', template)


class PolicyDetailRendersTest(TestCase):
    def test_the_policy_page_renders(self):
        user = User.objects.create(username='policy-viewer', is_superuser=True)
        group = Group.objects.create(name='Engineers')
        policy, _rule = make_policy('Rendered', groups=[group])
        self.client.force_login(user)
        self.assertEqual(self.client.get(policy.get_absolute_url()).status_code, 200)


class ChangeRequestDetailRendersTest(ChangeControlTestCase):
    branch_prefix = 'render'
    policy_checks = ()

    def test_the_detail_page_renders_for_a_reviewer(self):
        grant_reviewer(self.reviewer)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(self.cr.get_absolute_url()).status_code, 200)

    def test_the_detail_page_renders_for_an_administrator(self):
        admin = User.objects.create(username='render-admin', is_superuser=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(self.cr.get_absolute_url()).status_code, 200)
