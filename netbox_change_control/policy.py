"""
Policy matching and evaluation.

Matching answers "which policies govern this branch?". Evaluation answers "are those
policies satisfied?". Both are pure functions over the database so they can be tested
without a request.
"""

from dataclasses import dataclass, field

from netbox_change_control.choices import ChangeRequestStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeRequestPolicy, Policy

__all__ = (
    'PolicyEvaluation',
    'RuleEvaluation',
    'branch_last_change_time',
    'evaluate_change_request',
    'get_touched_object_types',
    'match_policies',
    'refresh_status',
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


def get_touched_object_types(branch):
    """
    Return the set of ObjectType PKs which the branch modifies.

    Reads netbox-branching's ChangeDiff, which holds one row per changed object.
    """
    from netbox_branching.models import ChangeDiff

    return set(ChangeDiff.objects.filter(branch=branch).values_list('object_type_id', flat=True).distinct())


def _conditions_match(policy, branch):
    """
    Evaluate a policy's condition set against each changed object in the branch. The policy
    matches when any single changed object satisfies the conditions.
    """
    if not policy.conditions:
        return True

    from extras.conditions import ConditionSet, InvalidCondition
    from netbox_branching.models import ChangeDiff

    condition_set = ConditionSet(policy.conditions)
    for diff in ChangeDiff.objects.filter(branch=branch).iterator():
        # Evaluate the state the branch proposes, not the state of main.
        #
        # `modified` is the object as the branch leaves it, and is None for a deletion, where
        # `original` holds what is being removed. `current` is deliberately not consulted: it
        # describes main, so a policy would have matched on somebody else's concurrent edit
        # rather than on the change under review.
        data = diff.modified or diff.original or {}
        try:
            if condition_set.eval(data):
                return True
        except InvalidCondition:
            # A condition referencing a field this object type does not have simply does
            # not match; it must not break evaluation of the remaining objects.
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
        else:
            # An unscoped policy applies to every branch.
            names = []
        if not _conditions_match(policy, branch):
            continue
        results.append((policy, names))

    return results


def sync_policies(change_request):
    """
    Attach every matching policy to the change request and drop stale automatic bindings.

    Bindings the author added by hand (matched=False) are left alone.
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
                matched=True,
                matched_object_types=names,
            )

    stale = [binding.pk for policy_id, binding in existing.items() if binding.matched and policy_id not in matches]
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
    stale = [
        r
        for r in reviews
        if r.branch_change_time is not None and latest_change is not None and latest_change > r.branch_change_time
    ]
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


def _emit_lifecycle_event(status, change_request):
    """
    Put the matching lifecycle event through NetBox's event pipeline, so an event rule can
    fire a webhook or run a script on the transition.
    """
    from netbox_change_control import events

    event_type = {
        ChangeRequestStatusChoices.NEEDS_REVIEW: events.CHANGE_REQUEST_REVIEW_REQUESTED,
        ChangeRequestStatusChoices.APPROVED: events.CHANGE_REQUEST_APPROVED,
        ChangeRequestStatusChoices.REJECTED: events.CHANGE_REQUEST_REJECTED,
    }.get(status)
    if event_type is not None:
        events.emit(change_request, event_type)


def refresh_status(change_request):
    """
    Recompute a change request's status from its current policy evaluation.

    `status` is a cached view of the evaluation, so it must be refreshed on every path that
    can change the outcome: a review added, edited or removed, or a policy attached or
    detached. Signal receivers call this so no caller can forget.

    Terminal statuses are never reopened.
    """
    if change_request.status in ChangeRequestStatusChoices.TERMINAL:
        return change_request.status

    evaluation = evaluate_change_request(change_request)
    if evaluation.rejections:
        status = ChangeRequestStatusChoices.REJECTED
    elif evaluation.satisfied:
        status = ChangeRequestStatusChoices.APPROVED
    else:
        status = ChangeRequestStatusChoices.NEEDS_REVIEW

    if status != change_request.status:
        change_request.status = status
        change_request.save(update_fields=['status'])

        # Announce only on an actual transition, so a busy request does not spam reviewers
        # or fire a webhook per save.
        from netbox_change_control.notifications import notify_status_change

        notify_status_change(change_request, status, evaluation)
        _emit_lifecycle_event(status, change_request)

        if status == ChangeRequestStatusChoices.APPROVED:
            # Reaching Approved is the moment a merge becomes possible, so refresh the checks
            # here. A branch edit invalidates the reviews but not the stored check results, so
            # without this a request could be edited to introduce a conflict, re-approved, and
            # then merged against a stale "no conflicts" row.
            #
            # run_checks triggers the auto-merge attempt itself once the results are current,
            # so it must not also be called here or the merge would be enqueued twice.
            from netbox_change_control.checks import run_checks

            run_checks(change_request)

    return status
