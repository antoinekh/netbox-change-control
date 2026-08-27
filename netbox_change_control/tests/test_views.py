"""
Tests for the review submission view.
"""

from django.test import RequestFactory, TestCase
from netbox_branching.models import Branch
from users.models import User

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequest, Review
from netbox_change_control.tests.base import ChangeControlTestCase
from netbox_change_control.views import SubmitReviewView


class SubmitReviewTest(ChangeControlTestCase):
    branch_prefix = 'view'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # The view restricts the reviewer queryset, so the submitter needs to see users.
        cls.reviewer.is_superuser = True
        cls.reviewer.save()

    def _post(self, decision, comment=''):
        request = RequestFactory().post('/', {'decision': decision, 'comment': comment})
        request.user = self.reviewer
        # The view adds messages, which need a backing store on a synthetic request.
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = {}
        request._messages = FallbackStorage(request)
        return SubmitReviewView().post(request, pk=self.cr.pk)

    def test_submitting_an_approval_records_it(self):
        self._post(ReviewDecisionChoices.APPROVE, 'looks good')
        review = Review.objects.get(change_request=self.cr, reviewer=self.reviewer)
        self.assertEqual(review.decision, ReviewDecisionChoices.APPROVE)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_a_failed_edit_does_not_destroy_the_existing_review(self):
        """
        Requesting changes without a comment fails validation. The reviewer's previous
        approval must survive; an earlier version wrote first and deleted on failure.
        """
        self._post(ReviewDecisionChoices.APPROVE, 'looks good')
        self.assertEqual(Review.objects.filter(change_request=self.cr).count(), 1)

        self._post(ReviewDecisionChoices.REJECT, '')

        review = Review.objects.get(change_request=self.cr, reviewer=self.reviewer)
        self.assertEqual(review.decision, ReviewDecisionChoices.APPROVE)
        self.assertEqual(review.comment, 'looks good')
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)

    def test_a_failed_first_submission_creates_nothing(self):
        self._post(ReviewDecisionChoices.REJECT, '')
        self.assertEqual(Review.objects.filter(change_request=self.cr).count(), 0)

    def test_resubmitting_replaces_rather_than_duplicates(self):
        self._post(ReviewDecisionChoices.APPROVE, 'first')
        self._post(ReviewDecisionChoices.REJECT, 'changed my mind')

        self.assertEqual(Review.objects.filter(change_request=self.cr).count(), 1)
        review = Review.objects.get(change_request=self.cr)
        self.assertEqual(review.decision, ReviewDecisionChoices.REJECT)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.REJECTED)


class ReviewEditPermissionTest(TestCase):
    """
    A review is a personal statement. Anyone holding change_review could otherwise turn a
    colleague's "request changes" into an approval, which defeats the gate with one form
    post, or reassign a review to forge somebody else's position.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission

        cls.mine = User.objects.create(username='mine')
        cls.theirs = User.objects.create(username='theirs')
        cls.admin = User.objects.create(username='root', is_superuser=True)
        cls.author = User.objects.create(username='author2')

        branch = Branch.objects.create(name='review-perms')
        cls.cr = ChangeRequest.objects.create(branch=branch, title='T', requester=cls.author)
        cls.my_review = Review.objects.create(
            change_request=cls.cr, reviewer=cls.mine, decision=ReviewDecisionChoices.APPROVE
        )
        cls.their_review = Review.objects.create(
            change_request=cls.cr,
            reviewer=cls.theirs,
            decision=ReviewDecisionChoices.REJECT,
            comment='no',
        )

        # A full change permission on reviews, as an ordinary reviewer would hold.
        permission = ObjectPermission.objects.create(name='edit-reviews', actions=['view', 'change'])
        permission.users.add(cls.mine)
        permission.object_types.set([ContentType.objects.get_for_model(Review)])

    def _edit_url(self, review):
        from django.urls import reverse

        return reverse('plugins:netbox_change_control:review_edit', args=[review.pk])

    def test_a_reviewer_can_edit_their_own_review(self):
        self.client.force_login(self.mine)
        self.assertEqual(self.client.get(self._edit_url(self.my_review)).status_code, 200)

    def test_a_reviewer_cannot_edit_somebody_elses_review(self):
        self.client.force_login(self.mine)
        self.assertEqual(self.client.get(self._edit_url(self.their_review)).status_code, 404)

    def test_a_superuser_can_edit_any_review(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self._edit_url(self.their_review)).status_code, 200)

    def test_the_form_cannot_reassign_the_review(self):
        """
        Neither the reviewer nor the change request is exposed, so a post cannot move a
        review onto another person or another request.
        """
        from netbox_change_control import forms

        fields = set(forms.ReviewEditForm().fields)
        self.assertNotIn('reviewer', fields)
        self.assertNotIn('change_request', fields)
        self.assertIn('decision', fields)
        self.assertIn('comment', fields)

    def test_posting_a_reviewer_is_ignored(self):
        self.client.force_login(self.mine)
        self.client.post(
            self._edit_url(self.my_review),
            {
                'decision': ReviewDecisionChoices.COMMENT,
                'comment': 'edited',
                'reviewer': self.theirs.pk,
                'change_request': self.cr.pk,
            },
        )
        self.my_review.refresh_from_db()
        self.assertEqual(self.my_review.reviewer, self.mine)
        self.assertEqual(self.my_review.comment, 'edited')
