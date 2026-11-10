"""
Every status transition lives in one module.

When the moves were spread across the model, the policy module, a signal receiver and the
views, one transition was announced twice and a stale one could undo a merge. These pin the
single home, so a move added elsewhere fails here rather than in production.
"""

import re
from pathlib import Path
from unittest.mock import patch

from netbox_change_control.choices import ChangeRequestStatusChoices
from netbox_change_control.events import CHANGE_REQUEST_REVIEW_REQUESTED, CHANGE_REQUEST_SUBMITTED
from netbox_change_control.tests.base import ChangeControlTestCase

PACKAGE = Path(__file__).resolve().parent.parent


class OneHomeForTransitionsTest(ChangeControlTestCase):
    branch_prefix = 'lifecycle'
    submitted = False

    def test_only_the_lifecycle_writes_the_status(self):
        writers = set()
        for path in PACKAGE.rglob('*.py'):
            if {'tests', 'migrations'} & set(path.relative_to(PACKAGE).parts):
                continue
            if re.search(r"update_fields=\['status'\]|\.update\(status=", path.read_text()):
                writers.add(path.relative_to(PACKAGE).as_posix())
        self.assertEqual(writers, {'lifecycle.py'})

    def test_submitting_announces_itself_once(self):
        with patch('extras.events.flush_events') as flush:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(self.cr.submit())

        emitted = [event['event_type'] for call in flush.call_args_list for event in call.args[0]]
        self.assertEqual(emitted.count(CHANGE_REQUEST_SUBMITTED), 1)
        self.assertEqual(emitted.count(CHANGE_REQUEST_REVIEW_REQUESTED), 1)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.NEEDS_REVIEW)
