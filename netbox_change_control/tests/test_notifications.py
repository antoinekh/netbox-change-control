"""
Tests for reviewer notifications.
"""

from django.test import TestCase
from users.models import Group, User

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.events import CHANGE_REQUEST_APPROVED, CHANGE_REQUEST_REVIEW_REQUESTED
from netbox_change_control.models import ChangeRequest, ChangeRequestPolicy, Review
from netbox_change_control.notifications import pending_reviewers
from netbox_change_control.policy import refresh_status
from netbox_change_control.tests.base import add_rule, make_branch, make_policy


class NotificationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.engineers = Group.objects.create(name='Engineers')
        cls.leads = Group.objects.create(name='Leads')
        cls.eng = User.objects.create(username='eng')
        cls.eng.groups.add(cls.engineers)
        cls.lead = User.objects.create(username='lead')
        cls.lead.groups.add(cls.leads)
        cls.requester = User.objects.create(username='requester')
        cls.requester.groups.add(cls.engineers)

        cls.policy, cls.eng_rule = make_policy('Two rules', groups=[cls.engineers], rule_name='Engineer')
        cls.lead_rule = add_rule(cls.policy, 'Lead', groups=[cls.leads])

    def setUp(self):
        self.branch = make_branch('notif', self._testMethodName)
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='T', requester=self.requester)
        ChangeRequestPolicy.objects.create(change_request=self.cr, policy=self.policy)
        self.cr.refresh_from_db()

    def _notifications(self, user):
        from extras.models import Notification

        return Notification.objects.filter(user=user)

    def test_pending_reviewers_excludes_the_requester(self):
        users = set(pending_reviewers(self.cr).values_list('username', flat=True))
        self.assertNotIn('requester', users)
        self.assertEqual(users, {'eng', 'lead'})

    def test_pending_reviewers_skips_satisfied_rules(self):
        Review.objects.create(change_request=self.cr, reviewer=self.eng, decision=ReviewDecisionChoices.APPROVE)
        users = set(pending_reviewers(self.cr).values_list('username', flat=True))
        self.assertEqual(users, {'lead'})

    def test_outstanding_reviewers_are_notified(self):
        refresh_status(self.cr)
        self.assertEqual(self._notifications(self.eng).count(), 1)
        self.assertEqual(self._notifications(self.eng).first().event_type, CHANGE_REQUEST_REVIEW_REQUESTED)

    def test_the_requester_is_not_asked_to_review(self):
        refresh_status(self.cr)
        self.assertEqual(self._notifications(self.requester).count(), 0)

    def test_requester_is_notified_on_approval(self):
        Review.objects.create(change_request=self.cr, reviewer=self.eng, decision=ReviewDecisionChoices.APPROVE)
        Review.objects.create(change_request=self.cr, reviewer=self.lead, decision=ReviewDecisionChoices.APPROVE)
        self.cr.refresh_from_db()
        self.assertEqual(self.cr.status, ChangeRequestStatusChoices.APPROVED)
        notification = self._notifications(self.requester).first()
        self.assertIsNotNone(notification)
        self.assertEqual(notification.event_type, CHANGE_REQUEST_APPROVED)

    def test_repeat_notification_updates_rather_than_failing(self):
        """
        Notification is unique per (object, user), so a second transition must reuse the row
        and mark it unread again.
        """
        from django.utils import timezone
        from extras.models import Notification

        refresh_status(self.cr)
        Notification.objects.filter(user=self.eng).update(read=timezone.now())

        # Force a transition back into needs-review.
        self.cr.status = ChangeRequestStatusChoices.DRAFT
        self.cr.save(update_fields=['status'])
        refresh_status(self.cr)

        self.assertEqual(self._notifications(self.eng).count(), 1)
        self.assertIsNone(self._notifications(self.eng).first().read)


class EventTypeRenderingTest(TestCase):
    """
    Our event types have to survive being rendered.

    NetBox's notification list renders `{{ notification.event }}`, and `EventType.__str__`
    returns its text unchanged. Registering that text with `gettext_lazy` returns a proxy, so
    `str()` raises `TypeError: __str__ returned non-string`, and the whole notification page
    returns a 500 for anyone holding one of our notifications. NetBox registers its own event
    types with eager `gettext` for the same reason.
    """

    OUR_EVENTS = (
        CHANGE_REQUEST_REVIEW_REQUESTED,
        CHANGE_REQUEST_APPROVED,
    )

    def test_every_event_type_stringifies(self):
        from netbox.registry import registry

        for name in self.OUR_EVENTS:
            with self.subTest(event=name):
                event = registry['event_types'].get(name)
                self.assertIsNotNone(event, f'{name} is not registered')
                self.assertIsInstance(event.text, str, f'{name} was registered with a lazy string')
                self.assertIsInstance(str(event), str)

    def test_the_notification_list_renders(self):
        """
        The page the user actually opens from the bell.
        """
        from django.urls import reverse

        from netbox_change_control.notifications import notify_status_change

        group = Group.objects.create(name='Bell engineers')
        reviewer = User.objects.create(username='bell-reviewer')
        reviewer.groups.add(group)
        requester = User.objects.create(username='bell-requester')
        policy, _rule = make_policy('Bell policy', groups=[group])

        change_request = ChangeRequest.objects.create(
            branch=make_branch('bell', self._testMethodName),
            title='Ring the bell',
            requester=requester,
        )
        ChangeRequestPolicy.objects.create(change_request=change_request, policy=policy)
        notify_status_change(change_request, ChangeRequestStatusChoices.NEEDS_REVIEW)
        self.assertTrue(reviewer.notifications.exists())

        self.client.force_login(reviewer)
        response = self.client.get(reverse('account:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Ring the bell', response.content.decode())
