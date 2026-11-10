"""
Views and the REST API check permissions through one module.

When each spelled its own permission names and restricted lookups, the interface and the API
drifted apart: one honoured an object constraint or an ownership rule that the other ignored.
These pin the single home, so a check written elsewhere fails here.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

PACKAGE = Path(__file__).resolve().parent.parent
CALLERS = [*(PACKAGE / 'views').glob('*.py'), *(PACKAGE / 'api').glob('*.py')]


class PermissionsLayerTest(SimpleTestCase):
    def test_no_view_spells_a_permission_name_in_a_check(self):
        offenders = [
            f'{path.relative_to(PACKAGE)}:{number}'
            for path in CALLERS
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"has_perm\(\s*['\"]", line)
        ]
        self.assertEqual(offenders, [])

    def test_no_view_restricts_a_queryset_by_hand(self):
        offenders = [
            f'{path.relative_to(PACKAGE)}:{number}'
            for path in CALLERS
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if '.restrict(' in line
        ]
        self.assertEqual(offenders, [])
