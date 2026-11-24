"""
The change request state machine, checked against its documentation and its invariants.

The transition table in docs/change-requests.md is the specification. Seeded random walks drive
a change request through real actions, the way the interface, the REST API and netbox-branching
do, and after every step check that each status move is a documented row, that the history
recorded it, and that the invariants hold. A failure names its seed and its steps, so it can be
replayed.
"""

import random
from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

from core.models import ObjectChange
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from netbox_branching.models import Branch
from netbox_branching.signals import post_merge, post_revert
from netbox_branching.utilities import activate_branch
from users.models import Group, User

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    HistoryActionChoices,
    MergeCheckStatusChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.models import ChangeRequest, MergeCheck, Review
from netbox_change_control.receivers.status import invalidate_approval_on_branch_change
from netbox_change_control.tests.base import docs_page, make_branch, make_diff, make_policy
from netbox_change_control.validators import require_approved_change_request

Status = ChangeRequestStatusChoices
History = HistoryActionChoices

LABELS = {str(label): value for value, label, _color in Status.CHOICES}
GROUPS = {
    'Any open state': Status.OPEN,
    'Any other status': tuple(value for value in LABELS.values() if value != Status.COMPLETED),
}

# The status each history entry records the request moving to. A revert moves it only from
# Completed, and the other entries record a step that moves no status.
MOVES = {
    History.SUBMITTED: Status.NEEDS_REVIEW,
    History.NEEDS_REVIEW: Status.NEEDS_REVIEW,
    History.APPROVED: Status.APPROVED,
    History.REJECTED: Status.REJECTED,
    History.RETURNED_TO_DRAFT: Status.DRAFT,
    History.MERGED: Status.COMPLETED,
    History.ABANDONED: Status.ABANDONED,
    History.REOPENED: Status.DRAFT,
}

# Only reachable with the merge gate switched off, which the walks keep on.
UNREACHED_WITH_THE_GATE_ON = {(status, Status.COMPLETED) for status in GROUPS['Any other status']} - {
    (Status.APPROVED, Status.COMPLETED)
}

WALKS = 30
STEPS = 20

# Each documented transition, made on purpose. The random walks then explore the combinations.
SCENARIOS = (
    ('submit', 'abandon', 'reopen', 'abandon'),
    ('submit', 'approve', 'pass_check', 'merge', 'revert'),
    ('submit', 'approve', 'edit_branch', 'approve', 'reject', 'withdraw_review'),
    ('submit', 'reject', 'approve', 'return_to_draft'),
    ('submit', 'reject', 'return_to_draft'),
    ('submit', 'return_to_draft'),
    ('submit', 'approve', 'move_window', 'abandon'),
    ('submit', 'approve', 'abandon'),
    ('submit', 'reject', 'delete_branch'),
    ('submit', 'reject', 'abandon'),
)


def documented_transitions():
    """
    The (from, to) pairs the "Every transition" table of docs/change-requests.md lists.
    """
    section = docs_page('change-requests.md').split('### Every transition', 1)[1].split('\n### ', 1)[0]
    transitions = set()
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 2 or cells[0] in ('From', '') or set(cells[0]) <= {'-'} or cells[0] == '*(none)*':
            continue
        sources = GROUPS.get(cells[0]) or tuple(LABELS[name.strip()] for name in cells[0].split(','))
        transitions.update((source, LABELS[cells[1]]) for source in sources)
    return transitions


class BranchClock:
    """
    Stands in for the branch's newest staged change. Test branches are not provisioned, so they
    have no change log of their own to read.
    """

    def __init__(self):
        self.time = None

    def get_unmerged_changes(self):
        clock = self

        class Changes:
            def order_by(self, *args):
                return self

            def values_list(self, *args, **kwargs):
                return self

            def first(self):
                return clock.time

        return Changes()


class Walk:
    """
    One change request driven by random actions, with the state the invariants compare against.
    """

    def __init__(self, test, seed):
        self.test = test
        self.rng = random.Random(seed)
        self.steps = []
        self.branch = make_branch('walk', f'{test._testMethodName}-{seed}')
        self.spare = make_branch('walk-spare', f'{test._testMethodName}-{seed}')
        self.cr = ChangeRequest.objects.create(branch=self.branch, title='Walk', requester=test.requester)
        self.object_id = 0
        self.record = None

    def reload(self):
        return ChangeRequest.objects.get(pk=self.cr.pk)

    # Actions. Each returns False when it does not apply to the current state.

    def submit(self, cr):
        return cr.can_be_submitted and cr.submit()

    def review(self, cr, decision):
        if not cr.is_open:
            return False
        review = Review.objects.filter(change_request=cr, reviewer=self.test.reviewer).first() or Review(
            change_request=cr, reviewer=self.test.reviewer
        )
        review.decision = decision
        review.comment = 'Walk' if decision == ReviewDecisionChoices.REJECT else ''
        review.full_clean()
        review.save(refresh_snapshot=True)
        return True

    def approve(self, cr):
        return self.review(cr, ReviewDecisionChoices.APPROVE)

    def reject(self, cr):
        return self.review(cr, ReviewDecisionChoices.REJECT)

    def withdraw_review(self, cr):
        return bool(Review.objects.filter(change_request=cr).delete()[0])

    def return_to_draft(self, cr):
        return cr.can_return_to_draft and cr.return_to_draft()

    def abandon(self, cr):
        return cr.can_be_abandoned and cr.abandon()

    def reopen(self, cr):
        return cr.can_be_reopened and cr.reopen()

    def edit_branch(self, cr):
        if cr.branch_deleted or cr.is_completed:
            return False
        self.test.clock.time = timezone.now()
        self.object_id += 1
        make_diff(self.branch, object_id=self.object_id)
        # An edit inside a branch also writes an ObjectChange there, which the plugin reacts to.
        with activate_branch(self.branch):
            invalidate_approval_on_branch_change(sender=ObjectChange, instance=None)
        return True

    def move_window(self, cr):
        now = timezone.now()
        cr.scheduled_start, cr.scheduled_end = self.rng.choice(
            [(None, None), (now - timedelta(hours=1), now + timedelta(hours=1)), (now + timedelta(hours=1), None)]
        )
        return self.save_if_valid(cr)

    def report_check(self, cr, status):
        check = MergeCheck.objects.filter(change_request=cr, name='ci').first()
        if check is None:
            return False
        check.status = status
        return self.save_if_valid(check)

    def pass_check(self, cr):
        return self.report_check(cr, MergeCheckStatusChoices.SUCCESS)

    def fail_check(self, cr):
        return self.report_check(cr, MergeCheckStatusChoices.FAILURE)

    def move_to_another_branch(self, cr):
        cr.branch = self.spare
        return self.save_if_valid(cr)

    def merge(self, cr):
        if cr.branch_deleted or not require_approved_change_request(self.branch).permitted:
            return False
        post_merge.send(sender=Branch, branch=self.branch, user=self.test.requester)
        return True

    def revert(self, cr):
        if cr.branch_deleted or not cr.is_completed:
            return False
        post_revert.send(sender=Branch, branch=self.branch, user=self.test.requester)
        return True

    def delete_branch(self, cr):
        if cr.branch_deleted:
            return False
        Branch.objects.get(pk=self.branch.pk).delete()
        return True

    @staticmethod
    def save_if_valid(obj):
        try:
            obj.full_clean()
        except ValidationError:
            return False
        obj.save()
        return True

    # Weighted so a walk often lines up an approval, a passing check and a merge, which is what
    # reaches the end of the lifecycle; the actions that end a walk early come up less.
    ACTIONS: ClassVar[dict[str, int]] = {
        'submit': 3,
        'approve': 5,
        'reject': 1,
        'withdraw_review': 1,
        'return_to_draft': 1,
        'abandon': 1,
        'reopen': 2,
        'edit_branch': 1,
        'move_window': 1,
        'pass_check': 3,
        'fail_check': 1,
        'move_to_another_branch': 1,
        'merge': 5,
        'revert': 3,
        'delete_branch': 1,
    }

    def random_step(self):
        self.step(self.rng.choices(list(self.ACTIONS), weights=list(self.ACTIONS.values()))[0])

    def step(self, action):
        before = self.reload()
        history = before.history.count()
        applied = getattr(self, action)(self.reload())
        self.steps.append(f'{action}{"" if applied else " (no-op)"}')
        after = self.reload()
        self.check(before, after, list(after.history.order_by('time', 'pk')[history:]))

    # Invariants.

    def fail(self, message):
        self.test.fail(f'{message}\nSteps: {", ".join(self.steps)}')

    def check(self, before, after, entries):
        status = before.status
        for entry in entries:
            target = MOVES.get(entry.action)
            if entry.action == History.REVERTED and status == Status.COMPLETED:
                target = Status.DRAFT
            if target is None or target == status:
                continue
            if (status, target) not in self.test.documented:
                self.fail(f'Undocumented transition {status} -> {target} on {entry.action}.')
            self.test.reached.add((status, target))
            status = target
        if status != after.status:
            self.fail(f'The history ends on {status}, the request is {after.status}.')

        if after.is_open and after.branch_deleted:
            self.fail('An open request has no branch.')
        if after.branch_id not in (self.branch.pk, None) or after.requester_id != before.requester_id:
            self.fail('The branch or the requester changed.')
        if after.cached_conflicted != bool(after.conflicts):
            self.fail('The cached conflict flag disagrees with the branch.')
        if after.cached_ready_to_merge != after.is_ready_to_merge:
            self.fail('The cached readiness disagrees with the merge gate.')

        if after.status in Status.UNDER_REVIEW:
            evaluation = after.evaluate()
            expected = (
                Status.REJECTED
                if evaluation.rejections
                else Status.APPROVED
                if evaluation.satisfied
                else Status.NEEDS_REVIEW
            )
            if after.status != expected:
                self.fail(f'The status is {after.status}, the evaluation says {expected}.')

        record = (
            sorted(after.checks.values_list('name', 'status', 'summary')),
            sorted(after.policy_bindings.values_list('policy_id', flat=True)),
            [getattr(after, name) for name in ChangeRequest.RECORD_FIELDS],
        )
        if after.is_open:
            self.record = None
        elif self.record is None or before.is_open:
            self.record = record
        elif record != self.record:
            self.fail('A finished request changed its checks, its policies or its record.')


class StateMachineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Walkers')
        cls.reviewer = User.objects.create(username='walk-reviewer')
        cls.reviewer.groups.add(cls.group)
        cls.requester = User.objects.create(username='walk-requester')
        make_policy('Walk policy', groups=[cls.group], checks=['ci'])
        cls.documented = documented_transitions()

    def setUp(self):
        self.reached = set()
        self.clock = BranchClock()
        patcher = patch.object(Branch, 'get_unmerged_changes', self.clock.get_unmerged_changes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_documentation_lists_transitions(self):
        self.assertIn((Status.DRAFT, Status.NEEDS_REVIEW), self.documented)
        self.assertGreater(len(self.documented), 15, 'The transition table was not found or not parsed.')

    def test_every_documented_transition_keeps_every_rule(self):
        for number, actions in enumerate(SCENARIOS):
            with self.subTest(scenario=actions):
                self.clock.time = None
                walk = Walk(self, f'scenario-{number}')
                for action in actions:
                    walk.step(action)

        unreached = self.documented - self.reached - UNREACHED_WITH_THE_GATE_ON
        self.assertEqual(unreached, set(), 'Documented transitions no scenario makes: add one.')

    def test_random_walks_keep_every_rule(self):
        for seed in range(WALKS):
            with self.subTest(seed=seed):
                self.clock.time = None
                walk = Walk(self, seed)
                for _ in range(STEPS):
                    walk.random_step()
