"""
Opening a change request reads; it does not write.

The page used to create the rows of checks registered since the request was created, and the
merge gate it consults could attach policies. A page view is a read.
"""

from unittest.mock import patch

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from users.models import User

from netbox_change_control.checks import CheckResult, _registry, register_check
from netbox_change_control.tests.base import ChangeControlTestCase


class PageReadsTest(ChangeControlTestCase):
    branch_prefix = 'page-reads'

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(User.objects.create(username='page-reader', is_superuser=True))
        self.saved_registry = dict(_registry)
        self.addCleanup(lambda: (_registry.clear(), _registry.update(self.saved_registry)))

    def _get(self):
        return self.client.get(self.cr.get_absolute_url())

    def test_opening_a_change_request_writes_nothing(self):
        register_check('late-check', 'Registered later', lambda cr: CheckResult.passed())
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self._get().status_code, 200)
        writes = [
            q['sql']
            for q in queries.captured_queries
            if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        self.assertEqual(writes, [])

    def test_a_check_with_no_row_is_still_shown(self):
        register_check('late-check', 'Registered later', lambda cr: CheckResult.passed())
        page = self._get().content.decode()
        self.assertIn('Registered later', page)
        self.assertFalse(self.cr.checks.filter(name='late-check').exists())

    def test_the_merge_gate_runs_once(self):
        from functools import cached_property

        from netbox_branching.models import Branch

        reads = []
        can_merge = Branch.can_merge

        def counted(branch):
            reads.append(branch.pk)
            return can_merge.func(branch)

        counted_property = cached_property(counted)
        counted_property.__set_name__(Branch, 'can_merge')
        with patch.object(Branch, 'can_merge', counted_property):
            self._get()
        self.assertEqual(len(reads), 1)
