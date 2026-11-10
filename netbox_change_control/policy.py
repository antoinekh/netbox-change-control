"""
Policy matching and evaluation.

Matching answers "which policies govern this branch?". Evaluation answers "are those
policies satisfied?". Both are pure functions over the database so they can be tested
without a request.
"""

from dataclasses import dataclass, field

from netbox_change_control.choices import (
    ChangeRequestStatusChoices,
    ConditionStateChoices,
    ReviewDecisionChoices,
)
from netbox_change_control.models import ChangeRequestPolicy, Policy

__all__ = (
    'PolicyEvaluation',
    'RuleEvaluation',
    'branch_last_change_time',
    'evaluate_change_request',
    'get_touched_object_types',
    'match_policies',
    'refresh_cached_state',
    'review_is_stale',
    'review_snapshot_time',
    'scope_may_have_drifted',
    'sync_policies',
)


@dataclass
class RuleEvaluation:
    """
    The outcome of one policy rule against the reviews on a change request.
    """

    rule: object
    approvals: list = field(default_factory=list)
    required: int = 1

    @property
    def count(self):
        return len(self.approvals)

    @property
    def satisfied(self):
        return self.count >= self.required

    @property
    def outstanding(self):
        return max(self.required - self.count, 0)


@dataclass
class PolicyEvaluation:
    """
    The outcome of every policy attached to a change request.
    """

    rules: list = field(default_factory=list)
    rejections: list = field(default_factory=list)
    stale: list = field(default_factory=list)

    @property
    def satisfied(self):
        """
        A change request is satisfied when no reviewer has requested changes and every
        rule has met its minimum. A request with no rules at all is not satisfied, because
        an unpoliced merge is exactly what this plugin exists to prevent.
        """
        if self.rejections:
            return False
        if not self.rules:
            return False
        return all(r.satisfied for r in self.rules)

    @property
    def outstanding(self):
        return sum(r.outstanding for r in self.rules)

    def reasons(self):
        """
        Every reason the request is not satisfied, including each rule's shortfall.

        For callers with no rule table in front of them: the merge gate's refusal message,
        the REST API, a log line.
        """
        return self.other_reasons() + [
            f'Rule "{rule.rule.name}" needs {rule.outstanding} more approval(s) of {rule.required}.'
            for rule in self.rules
            if not rule.satisfied
        ]

    def other_reasons(self):
        """
        Only the reasons a rule table does not already show.

        The change request page lists every rule with its count, so repeating "needs 1 more
        approval of 1" underneath says the same thing twice. A rejection, a stale review or
        the absence of any rule is not visible in that table, so it belongs here.
        """
        messages = []
        for review in self.rejections:
            messages.append(f'{review.reviewer} requested changes.')
        if self.stale:
            names = ', '.join(str(r.reviewer) for r in self.stale)
            messages.append(f'The branch changed after these reviews were submitted, so they no longer count: {names}.')
        if not self.rules:
            messages.append('No policy rules apply to this change request.')
        return messages


def branch_last_change_time(branch):
    """
    Return the timestamp of the most recent change staged in the branch, or None.

    Used to decide whether a review still reflects the branch it was made against. A deleted
    branch has no changes, so nothing can become stale against it.
    """
    if branch is None:
        return None
    return branch.get_unmerged_changes().order_by('-time').values_list('time', flat=True).first()


def review_is_stale(review, latest_change):
    """
    Whether the branch changed after `review` was given, `latest_change` being its newest change.

    A review with no recorded branch state was given on a branch that is gone, and nothing can
    change it any more.
    """
    if review.branch_change_time is None or latest_change is None:
        return False
    return latest_change > review.branch_change_time


def review_snapshot_time(branch):
    """
    The branch state a review is given against: its newest change, or now if it has none yet.

    An empty branch has no change to record, and a review recording nothing could never go
    stale, so approving an empty branch would cover whatever was added to it afterwards. Now
    is later than nothing in the branch and earlier than anything added to it. A deleted
    branch returns None: nothing can be added to it, so its reviews stay current.
    """
    if branch is None:
        return None
    from django.utils import timezone

    return branch_last_change_time(branch) or timezone.now()


def get_touched_object_types(branch):
    """
    Return the set of ObjectType PKs which the branch modifies.

    Reads netbox-branching's ChangeDiff, which holds one row per changed object.
    """
    from netbox_branching.models import ChangeDiff

    return set(ChangeDiff.objects.filter(branch=branch).values_list('object_type_id', flat=True).distinct())


def _condition_states(diff, condition_state):
    """
    The states a policy's conditions are evaluated against, for one changed object.

    `modified` is the object as the branch leaves it and is None for a deletion; `original` is
    the object before the change and is None for a creation. `current` is never offered: it
    describes main, so a policy would match on somebody else's concurrent edit rather than on
    the change under review.

    EITHER offers both sides, so `status == active` catches an object being switched off as
    well as one being switched on. AFTER and BEFORE narrow that deliberately.

    This governs the plain attribute names only. A condition written against `snapshots` sees
    both sides whatever is chosen here; see `_condition_payloads`.
    """
    if condition_state == ConditionStateChoices.BEFORE:
        candidates = (diff.original,)
    elif condition_state == ConditionStateChoices.AFTER:
        candidates = (diff.modified,)
    else:
        candidates = (diff.modified, diff.original)
    return [data for data in candidates if data]


def _condition_payloads(diff, condition_state):
    """
    What one changed object offers a condition set, as NetBox's event pipeline offers it.

    NetBox 4.7 taught conditions to read the two sides of a change directly: the `changed` and
    `unchanged` operators compare an attribute across them, and `snapshots.prechange.<attr>`
    reads one side by name. Both look for a `snapshots` key beside the object's own fields,
    which is what is added here, so a policy can finally say "only when the status changes to
    active" rather than "whenever the status reads active on either side".

    The naming follows the event pipeline exactly, because the conditions are NetBox's and an
    operator who has written an event rule has already learned it: `prechange` is the object
    before the change and is null on a creation, `postchange` is the object after and is null
    on a deletion. A change diff holds raw field values, the same shape as an event snapshot,
    so a choice field is compared as `status` and never as `status.value`.

    One payload is yielded per state `condition_state` asks for, and a bare `snapshots` payload
    where it asks for a state this change does not have. That last case is what lets a ruleset
    made only of snapshot operators still be evaluated on a creation under BEFORE, where there
    is no object to read attributes off at all.
    """
    snapshots = {'prechange': diff.original, 'postchange': diff.modified}
    states = _condition_states(diff, condition_state) or [{}]

    # `snapshots` is written last, so it wins over a field of that name on the object itself.
    # NetBox's own pipeline resolves the collision the same way.
    return [{**data, 'snapshots': snapshots} for data in states]


def _conditions_match(policy, branch, object_type_ids=None):
    """
    Evaluate a policy's condition set against each changed object in the branch. The policy
    matches when any single changed object satisfies the conditions.

    `object_type_ids` limits the objects read to the policy's own scope: a policy about circuits
    must not attach because some other object in the branch satisfies its conditions.
    """
    if not policy.conditions:
        return True

    from extras.conditions import ConditionSet, InvalidCondition
    from netbox_branching.models import ChangeDiff

    diffs = ChangeDiff.objects.filter(branch=branch)
    if object_type_ids is not None:
        diffs = diffs.filter(object_type_id__in=object_type_ids)

    condition_set = ConditionSet(policy.conditions)
    for diff in diffs.iterator():
        for data in _condition_payloads(diff, policy.condition_state):
            try:
                if condition_set.eval(data):
                    return True
            except InvalidCondition:
                # A condition referencing a field this object type does not have simply does
                # not match; it must not break evaluation of the remaining objects. NetBox 4.7
                # raises the same exception for a snapshot path which resolves in neither
                # side, so a typo in one goes the same way: it matches nothing.
                continue
    return False


def match_policies(branch):
    """
    Return the enabled policies which govern this branch, as a list of
    (policy, matched_object_type_names) tuples.
    """
    touched = get_touched_object_types(branch)
    results = []

    candidates = Policy.objects.filter(enabled=True).prefetch_related('object_types')
    for policy in candidates:
        scoped = list(policy.object_types.all())
        if scoped:
            matching = [ot for ot in scoped if ot.pk in touched]
            if not matching:
                continue
            names = [f'{ot.app_label}.{ot.model}' for ot in matching]
            object_type_ids = [ot.pk for ot in matching]
        else:
            # An unscoped policy applies to every branch.
            names = []
            object_type_ids = None
        if not _conditions_match(policy, branch, object_type_ids):
            continue
        results.append((policy, names))

    return results


def scope_may_have_drifted(change_request):
    """
    Cheap test for whether a full re-match could attach a policy which is not attached yet.

    `match_policies` is not cheap: it reads every enabled policy, prefetches its object types,
    and scans the branch diff once per policy carrying conditions. The merge gate is read
    whenever a merge button is rendered, including once per row of the change request list, so
    it must not pay that on every read.

    A policy can only newly match if it is enabled, is not attached already, and is either
    unscoped or scoped to an object type the branch actually touches. That is two indexed
    queries, and it is the whole candidate set, so a False here is a guarantee rather than a
    guess. Conditions can only narrow a policy further, never widen it, so a conditional
    policy which this misses could not have matched anyway.

    This deliberately says nothing about policies which should be *detached*. An attached
    policy that no longer matches asks for approvals the change no longer needs, which is
    tighter than the truth rather than looser, so the gate does not need to force that.
    """
    from django.db.models import Q

    if change_request.branch_deleted:
        return False

    touched = get_touched_object_types(change_request.branch)
    attached = ChangeRequestPolicy.objects.filter(change_request=change_request).values_list('policy_id', flat=True)

    return (
        Policy.objects.filter(enabled=True)
        .exclude(pk__in=attached)
        .filter(Q(object_types__in=touched) | Q(object_types__isnull=True))
        .exists()
    )


def sync_policies(change_request):
    """
    Attach every matching policy to the change request, and drop the ones that no longer match.
    """
    if change_request.branch_deleted:
        # The diff is gone, so there is nothing left to match against. Existing bindings are
        # left alone: they record which policies governed the change at the time.
        return ChangeRequestPolicy.objects.filter(change_request=change_request)

    matches = {policy.pk: names for policy, names in match_policies(change_request.branch)}

    existing = {
        binding.policy_id: binding for binding in ChangeRequestPolicy.objects.filter(change_request=change_request)
    }

    for policy_id, names in matches.items():
        if binding := existing.get(policy_id):
            if binding.matched_object_types != names:
                binding.matched_object_types = names
                binding.save(update_fields=['matched_object_types'])
        else:
            ChangeRequestPolicy.objects.create(
                change_request=change_request,
                policy_id=policy_id,
                matched_object_types=names,
            )

    stale = [binding.pk for policy_id, binding in existing.items() if policy_id not in matches]
    if stale:
        ChangeRequestPolicy.objects.filter(pk__in=stale).delete()

    return ChangeRequestPolicy.objects.filter(change_request=change_request)


def evaluate_change_request(change_request):
    """
    Evaluate every rule of every attached policy against the submitted reviews.
    """
    from netbox_change_control.models import PolicyRule

    reviews = list(change_request.reviews.select_related('reviewer').all())

    # Evaluating staleness needs one query for the branch, not one per review.
    latest_change = branch_last_change_time(change_request.branch)
    stale = [r for r in reviews if review_is_stale(r, latest_change)]
    stale_ids = {r.pk for r in stale}

    approvals = {
        r.reviewer_id: r for r in reviews if r.decision == ReviewDecisionChoices.APPROVE and r.pk not in stale_ids
    }
    rejections = [r for r in reviews if r.decision == ReviewDecisionChoices.REJECT and r.pk not in stale_ids]

    evaluation = PolicyEvaluation(rejections=rejections, stale=stale)

    policy_ids = ChangeRequestPolicy.objects.filter(change_request=change_request).values_list('policy_id', flat=True)

    rules = (
        PolicyRule.objects.filter(
            policy_id__in=policy_ids,
            policy__enabled=True,
        )
        .select_related('policy')
        .prefetch_related('groups', 'users')
    )

    for rule in rules:
        eligible = set(rule.eligible_users().values_list('pk', flat=True))
        matched = [review for uid, review in approvals.items() if uid in eligible]
        evaluation.rules.append(RuleEvaluation(rule=rule, approvals=matched, required=rule.min_reviews))

    return evaluation


def refresh_cached_state(change_request):
    """
    Recompute the two cached columns on a change request. Returns True if either moved.

    The change request list shows whether a branch conflicts with main and whether it is ready
    to merge. Read live, those cost about eleven queries per row: the conflict test asks the
    database twice, and the readiness test runs the whole merge gate. Fifty rows is five
    hundred queries for two columns.

    Both are cached instead, and refreshed on the events that can change them, which is what
    the callers of this function are. It is the same split the plugin already makes for
    `status`: a cache for display and filtering, never for a decision.

    `cached_gates_cleared` deliberately excludes the change window. A window opens because the
    clock moved, not because anything happened, so there is no event on which to refresh it;
    `cached_ready_to_merge` combines this flag with the window at read time, from fields the
    row already carries.

    It also computes the plugin's own gates directly rather than through `Branch.can_merge`.
    Going through the gate would re-enter policy matching, which writes, which lands back
    here. The cost is that a merge validator registered by another plugin is not reflected in
    the column; the change request page and the gate itself both still recompute in full.
    """
    from netbox_change_control.validators import blocking_checks

    if change_request.branch_deleted:
        conflicted = False
        gates_cleared = False
    else:
        conflicted = bool(change_request.conflicts)
        gates_cleared = (
            change_request.status == ChangeRequestStatusChoices.APPROVED
            and evaluate_change_request(change_request).satisfied
            and not blocking_checks(change_request)
        )

    if (change_request.cached_conflicted, change_request.cached_gates_cleared) == (conflicted, gates_cleared):
        return False

    change_request.cached_conflicted = conflicted
    change_request.cached_gates_cleared = gates_cleared
    change_request.save(update_fields=['cached_conflicted', 'cached_gates_cleared'])
    return True
