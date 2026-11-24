"""
Rendering details that are wrong in ways nobody reports.

A badge with an invalid colour class, a select that quietly resets, a timestamp in a different
format from the one next to it. None of these throws, so none of them shows up in a test that
only asserts a 200.
"""

import re
from pathlib import Path

from django.test import TestCase

from netbox_change_control.models import ChangeRequest, Review
from netbox_change_control.tests.base import grant

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


def grant_reviewer(user):
    """
    View the request, and be able to review it: the form is replaced by an explanation for a
    user who cannot, so without `add_review` there is no select to inspect at all.
    """
    grant(user, ['view'], ChangeRequest, name='view')
    grant(user, ['view', 'add'], Review, name='review')
