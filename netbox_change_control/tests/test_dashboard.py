"""
The My Reviews dashboard widget lists what is waiting on the signed-in user.

It follows the notifications, as the documentation says: a reviewer is told when a request
needs review, never while it is a draft the author is still holding.
"""

from django.test import RequestFactory

from netbox_change_control.dashboard import MyReviewsWidget
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy
from netbox_change_control.tests.base import ChangeControlTestCase, make_branch


class MyReviewsWidgetTest(ChangeControlTestCase):
    branch_prefix = 'widget'

    def _listed(self):
        request = RequestFactory().get('/')
        request.user = self.reviewer
        html = MyReviewsWidget(title='My Reviews').render(request)
        return {cr for cr in ChangeRequest.objects.all() if cr.get_absolute_url() in html}

    def test_a_request_needing_review_is_listed(self):
        self.assertIn(self.cr, self._listed())

    def test_a_draft_is_not_listed(self):
        draft = ChangeRequest.objects.create(
            branch=make_branch('widget-draft', self._testMethodName), title='Draft', requester=self.requester
        )
        ChangeRequestPolicy.objects.create(change_request=draft, policy=self.policy)

        self.assertNotIn(draft, self._listed())
