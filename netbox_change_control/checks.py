"""
Pre-merge checks.

A check is a named pass or fail result attached to a change request, in the spirit of commit
status checks on a hosted git service. Required checks gate the merge independently of
reviews, so a change can be approved by people and still be refused by a machine.

Two sources:

- **Registered checks** run in-process. Register a callable at startup and it runs whenever
  checks are refreshed.
- **Reported checks** come from outside. An external pipeline PATCHes a result onto the REST
  API. Declare them in `required_external_checks` so they are created as pending and block
  the merge until reported.
"""

import logging
from dataclasses import dataclass

from django.utils import timezone
from netbox.plugins import get_plugin_config

from netbox_change_control.choices import MergeCheckStatusChoices

__all__ = (
    'BUILTIN_CHECKS',
    'CheckResult',
    'CheckScope',
    'get_registered_checks',
    'register_builtin_checks',
    'register_check',
    'run_checks',
    'sync_checks',
)

logger = logging.getLogger('netbox.plugins.netbox_change_control.checks')

# name -> RegisteredCheck
_registry = {}


@dataclass
class CheckResult:
    """
    What a check function returns.
    """

    status: str
    summary: str = ''
    details_url: str = ''

    @classmethod
    def passed(cls, summary=''):
        return cls(MergeCheckStatusChoices.SUCCESS, summary)

    @classmethod
    def failed(cls, summary):
        return cls(MergeCheckStatusChoices.FAILURE, summary)

    @classmethod
    def skipped(cls, summary=''):
        return cls(MergeCheckStatusChoices.SKIPPED, summary)


class CheckScope:
    """
    When a check applies.

    ALWAYS is the default and matches the original behaviour: the check runs on every change
    request. POLICY makes it opt-in, so it runs only on requests carrying a policy which
    names it. That is how a check is made specific to the kind of change it belongs to, such
    as a peer sign-off that only matters for circuits, rather than appearing on every request
    and being skipped by hand.
    """

    ALWAYS = 'always'
    POLICY = 'policy'


@dataclass
class RegisteredCheck:
    name: str
    label: str
    func: object
    required: bool = True
    scope: str = CheckScope.ALWAYS


def register_check(name, label, func, required=True, scope=CheckScope.ALWAYS):
    """
    Register an in-process check.

    `func` takes a ChangeRequest and returns a CheckResult. Raising is allowed; the check is
    recorded as errored rather than breaking the page.

    `scope` decides where it applies. See CheckScope.
    """
    if scope not in (CheckScope.ALWAYS, CheckScope.POLICY):
        raise ValueError(f'Unknown check scope {scope!r}.')
    _registry[name] = RegisteredCheck(name=name, label=label, func=func, required=required, scope=scope)


def get_registered_checks():
    return dict(_registry)


def policy_check_names(change_request):
    """
    The check names asked for by the policies attached to this change request.
    """
    if change_request is None or change_request.pk is None:
        return set()
    names = set()
    for binding in change_request.policy_bindings.select_related('policy'):
        names.update(binding.policy.checks or [])
    return names


def expected_checks(change_request=None):
    """
    Return every check which should exist for this change request, as {name: (label, required)}.

    This is the single definition of "what checks apply", used both to create the rows and to
    decide whether the merge gate is satisfied. The gate must not rely on rows alone: a check
    registered after a change request was created has no row yet, and treating a missing row
    as passing would let it merge unchecked.

    Called without a change request it returns the checks which apply everywhere, which is
    what a caller wanting the global set gets.
    """
    expected = {
        check.name: (check.label, check.required) for check in _registry.values() if check.scope == CheckScope.ALWAYS
    }

    for entry in get_plugin_config('netbox_change_control', 'required_external_checks') or []:
        # Accept either 'name' or ('name', 'Label').
        if isinstance(entry, (list, tuple)):
            name, label = entry[0], entry[1]
        else:
            name, label = entry, entry
        expected[name] = (label, True)

    # A policy can ask for more. A name the registry does not know is an externally reported
    # check, the same as an entry in required_external_checks, so a policy can require a
    # result from a pipeline without that pipeline being wired into every change request.
    for name in policy_check_names(change_request):
        if check := _registry.get(name):
            expected[name] = (check.label, check.required)
        else:
            expected.setdefault(name, (name, True))

    return expected


def sync_checks(change_request):
    """
    Make sure a MergeCheck row exists for every registered and declared external check, and
    drop rows for checks which no longer exist.
    """
    from netbox_change_control.models import MergeCheck

    expected = expected_checks(change_request)

    existing = {c.name: c for c in MergeCheck.objects.filter(change_request=change_request)}

    for name, (label, required) in expected.items():
        if check := existing.get(name):
            if check.label != label or check.required != required:
                check.label = label
                check.required = required
                check.save(update_fields=['label', 'required'])
        else:
            MergeCheck.objects.create(
                change_request=change_request,
                name=name,
                label=label,
                required=required,
                status=MergeCheckStatusChoices.PENDING,
            )

    obsolete = [c.pk for name, c in existing.items() if name not in expected]
    if obsolete:
        MergeCheck.objects.filter(pk__in=obsolete).delete()

    return MergeCheck.objects.filter(change_request=change_request)


def run_checks(change_request):
    """
    Run every registered check and store its result. Externally reported checks are left
    untouched, since only their reporter knows the answer.
    """
    from netbox_change_control.models import MergeCheck

    sync_checks(change_request)

    applicable = expected_checks(change_request)
    rows = {row.name: row for row in MergeCheck.objects.filter(change_request=change_request)}

    for check in _registry.values():
        # A policy-scoped check that no attached policy asked for has no row and must not run.
        if check.name not in applicable:
            continue
        try:
            result = check.func(change_request)
        except Exception as e:
            logger.exception('Merge check %s raised', check.name)
            result = CheckResult(MergeCheckStatusChoices.ERROR, f'{type(e).__name__}: {e}'[:500])

        row = rows.get(check.name)
        if row is None:
            continue

        summary = result.summary[:500]
        if (row.status, row.summary, row.details_url) == (result.status, summary, result.details_url):
            # Nothing moved, so nothing is written. A re-run that finds the same answer is not
            # a change, and recording one would fill the changelog with noise and bury the
            # transitions that matter.
            continue

        row.status = result.status
        row.summary = summary
        row.details_url = result.details_url
        row.completed = timezone.now()
        # save(), not queryset.update(). update() writes straight to the database and fires no
        # post_save, so NetBox never recorded a change: a required check going from failed to
        # passed, which is what opens the gate, left no entry in the changelog at all. For a
        # plugin whose job is the record of who allowed what, "the pipeline went green at
        # 14:02" is exactly the fact worth keeping.
        row.save(update_fields=['status', 'summary', 'details_url', 'completed'])

    # An in-process check turning green can be the last gate a request was waiting on. The
    # saves above reach the MergeCheck receiver, but only when a result actually moved, so this
    # also covers the run where everything was already passing.
    from netbox_change_control.automerge import try_auto_merge
    from netbox_change_control.policy import refresh_cached_state

    # A check result moves whether the request is ready, which the list reads from a cache.
    refresh_cached_state(change_request)

    try_auto_merge(change_request)

    return MergeCheck.objects.filter(change_request=change_request)


#
# Built-in checks
#


def check_branch_has_changes(change_request):
    """
    A change request whose branch is empty has nothing to merge.
    """
    from netbox_branching.models import ChangeDiff

    if change_request.branch_deleted:
        return CheckResult.skipped('The branch no longer exists.')

    count = ChangeDiff.objects.filter(branch=change_request.branch).count()
    if count == 0:
        return CheckResult.failed('The branch contains no changes.')
    return CheckResult.passed(f'{count} object(s) changed.')


def check_no_conflicts(change_request):
    """
    Branching flags a conflict when main has changed the same fields as the branch. Merging
    through a real one silently discards somebody's work.

    Only conflicts where main has moved since the last sync count. Branching never advances a
    diff's baseline, so a field main touched before the sync stays flagged forever even
    though the branch already holds main's value; failing on that would train reviewers to
    acknowledge conflicts by reflex.
    """
    from netbox_change_control.conflicts import conflicting_diffs, stale_baseline_diffs

    if change_request.branch_deleted:
        return CheckResult.skipped('The branch no longer exists.')

    real = conflicting_diffs(change_request.branch)
    if real:
        sample = ', '.join(d.object_repr for d in real[:3])
        return CheckResult.failed(f'{len(real)} object(s) conflict with main: {sample}')

    if stale := stale_baseline_diffs(change_request.branch):
        return CheckResult.passed(
            f'No conflicts with main. {len(stale)} object(s) are flagged by branching but were '
            f'already reconciled by a sync.'
        )

    return CheckResult.passed('No conflicts with main.')


def check_threads_resolved(change_request):
    """
    Every comment thread on the Changes tab must be resolved before the branch merges.

    An open thread is an unanswered concern about a specific object. Merging past one loses
    the discussion, since the branch diff disappears once merged.
    """
    if change_request.branch_deleted:
        # Nothing to merge, so nothing to hold up. Its siblings already skipped here, and this
        # one failing instead made the documented promise that a branchless request skips its
        # checks true of three checks out of four.
        return CheckResult.skipped('The branch no longer exists.')

    open_threads = change_request.change_comments.filter(parent__isnull=True, resolved=False)
    count = open_threads.count()
    if not count:
        return CheckResult.passed('No unresolved comment threads.')

    # change_label rather than change_diff.object_repr: the diff is gone once the branch is
    # deleted, and reaching through the null relation would record this check as errored.
    objects = ', '.join(sorted({c.change_label or '(unknown object)' for c in open_threads[:3]}))
    return CheckResult.failed(f'{count} unresolved comment thread(s) on: {objects}')


def check_branch_not_stale(change_request):
    """
    Branching marks a branch stale once it is too far behind main to be synced safely.
    """
    if change_request.branch_deleted:
        return CheckResult.skipped('The branch no longer exists.')

    branch = change_request.branch
    if branch.is_stale:
        return CheckResult.failed('The branch is too far behind main to sync. Recreate it.')
    return CheckResult.passed('Branch is within the sync window.')


# The built-in checks, keyed by the name used in configuration.
BUILTIN_CHECKS = {
    'has-changes': ('Branch has changes', check_branch_has_changes),
    'no-conflicts': ('No conflicts with main', check_no_conflicts),
    'not-stale': ('Branch not stale', check_branch_not_stale),
    'threads-resolved': ('Comment threads resolved', check_threads_resolved),
}


def register_builtin_checks(names=None):
    """
    Register the built-in checks, and return the names registered.

    `names` selects a subset; None registers all of them. Registering makes a check
    *available*; a policy decides where it *applies*. Every built-in is registered with the
    policy scope, so a change request carries only the checks its policies ask for.

    That keeps one mechanism instead of two. A check which should apply to everything is a
    policy with no object types, which matches every branch.

    An unrecognised name is logged and skipped rather than raising, so a typo in
    configuration does not stop NetBox booting.
    """
    selected = []
    for name in BUILTIN_CHECKS if names is None else names:
        if name not in BUILTIN_CHECKS:
            logger.warning(
                "Unknown built-in check '%s' in enable_builtin_checks. Valid names: %s",
                name,
                ', '.join(sorted(BUILTIN_CHECKS)),
            )
            continue
        label, func = BUILTIN_CHECKS[name]
        register_check(name, label, func, scope=CheckScope.POLICY)
        selected.append(name)

    return selected
