"""
Event rule action types.

NetBox 4.7 opened the Event Rule action list to plugins. An action is a subclass of
`EventRuleAction`, and NetBox loads the `event_rule_actions` tuple at the bottom of this
module from `PluginConfig.ready()`, by its default resource path. Registering one puts a new
entry in the **Action type** dropdown on an event rule.

This closes the one direction the plugin could not go. A change request could already tell
the world it had been approved, because it emits lifecycle events an event rule can act on.
Nothing could act the other way: an ordinary change in NetBox had no way to say anything back
to the change request reviewing it, short of writing a custom script or calling the REST API
from outside.

A pre-merge check was the sharpest form of that gap. Checks are pluggable, but only in
Python: you write a function and register it in code, which means a deployment. An operator
who wanted "flag this branch when a live circuit is switched off" could describe the rule
precisely and still not express it. `ReportCheckAction` is that sentence, written in the
interface, on an ordinary event rule.

It pairs with the condition operators NetBox 4.7 added. `changed` and `snapshots.prechange.*`
are what let the rule's own conditions describe a transition rather than a value, which is
the same thing a policy condition can now say. See docs/event-rules.md.

Treat this as provisional. An event rule answers one change at a time, and it can only report
onto a change request which is already open, so a change made in the branch before that is
missed and nothing replays it. A merge gate is a question about the whole branch, which is a
different shape of question; if the mismatch proves to matter, this action may be removed in
favour of evaluating the branch's ChangeDiff rows the way `_conditions_match` already does.
"""

import logging

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from netbox.event_rules import EventRuleAction

from netbox_change_control.choices import MergeCheckStatusChoices

__all__ = (
    'ReportCheckAction',
    'event_rule_actions',
)

logger = logging.getLogger('netbox.plugins.netbox_change_control')


def _max_length(field_name):
    """
    The column's own limit, read from the model rather than repeated here.

    A number copied into this module is a number which drifts: widen the field one day and the
    action keeps cutting to the old width, or narrow it and every write raises instead.
    """
    from netbox_change_control.models import MergeCheck

    return MergeCheck._meta.get_field(field_name).max_length


class ReportCheckAction(EventRuleAction):
    """
    Record a pre-merge check result on the change request of the branch the event came from.

    The action reports a result for a check which already exists on the change request. It
    does not invent one, and that is deliberate: `sync_checks` deletes any row the
    configuration does not expect, so a check conjured here would vanish at the next
    evaluation and take its blocking result with it. Declaring the name is also what makes
    the check required, which is the whole point of reporting one. Name it in a policy's
    Checks field, or in `required_external_checks`, exactly as for a result reported over the
    REST API.

    Which change request the result lands on is not configured, because configuring it would
    make the rule useless: it would have to name one request, and a request lasts days while a
    rule lasts for ever. The action finds it from the event instead. See `_change_request`.
    """

    # A dotted namespace, which the base class recommends and which keeps the name clear of
    # both core and any other plugin. No hyphens: NetBox sanitizes the slug into a GraphQL
    # enum member and rejects one.
    slug = 'netbox_change_control.report_check'
    label = _('Change control: report a pre-merge check')
    description = _('Record a pre-merge check result on the change request the event belongs to')
    # The action operates on the change request the event implies, never on an object picked
    # on the rule. Leaving this None is what removes the object picker from the event rule
    # form, and makes NetBox reject a rule which supplies an object anyway.
    object_model = None

    def validate(self, *, action_object, action_data):
        """
        Reject a rule which cannot work, at the moment somebody saves it.

        Everything this action needs is in `action_data`, so a typo there is the whole failure
        mode. Catching it here means the operator is told on the form; the alternative is a
        rule that saves cleanly and then does nothing at all, silently, whenever it fires.
        """
        data = action_data or {}
        if not isinstance(data, dict):
            raise ValidationError(
                {'action_data': _('Action data must be an object, for example {"check": "safety-review"}.')}
            )

        name = data.get('check')
        if not name or not isinstance(name, str):
            raise ValidationError(
                {'action_data': _('Action data must name the check to report, as "check": "<name>".')}
            )

        status = data.get('status', MergeCheckStatusChoices.FAILURE)
        if status not in MergeCheckStatusChoices.values():
            raise ValidationError(
                {
                    'action_data': _('"{status}" is not a check status. Valid statuses: {valid}.').format(
                        status=status, valid=', '.join(MergeCheckStatusChoices.values())
                    )
                }
            )

        summary = data.get('summary')
        if summary is not None and not isinstance(summary, str):
            raise ValidationError({'action_data': _('"summary" must be text.')})

        # A summary which is too long is cut to fit when the result is written, so it is not
        # refused here. A details URL is not: cutting a link produces a link which goes
        # somewhere else, or nowhere, which is worse than having none. Say so on the form,
        # where it can still be corrected.
        details_url = data.get('details_url')
        if details_url is not None:
            if not isinstance(details_url, str):
                raise ValidationError({'action_data': _('"details_url" must be text.')})
            limit = _max_length('details_url')
            if len(details_url) > limit:
                raise ValidationError(
                    {'action_data': _('"details_url" must be {limit} characters or fewer.').format(limit=limit)}
                )

    def enqueue(self, *, event_rule, event_context, action_object, action_data):
        """
        Write the result.

        No configuration problem raises from here. A rule which cannot be satisfied is that
        rule's own fault, and the base class is explicit that such a rule must log and return:
        an exception would abandon whatever else NetBox was dispatching in the same batch. So
        a missing check, a missing change request and an over-long value are all logged and
        dropped, and every value written is bounded to what its column accepts.

        A database error still propagates, and must. By then the transaction which carried the
        originating change is already broken, and swallowing it here would hide a real failure
        behind a silent log line.

        The work is done inline rather than handed to a worker. It is one indexed lookup and
        at most one row written, which is cheaper than the job record that deferring it would
        cost, and doing it here keeps the result in the same transaction as the change that
        caused it.
        """
        from django.utils import timezone

        from netbox_change_control.models import MergeCheck

        # `action_data` arrives merged with the event payload, so a payload field could shadow
        # a key of ours. The rule's own configuration is read straight off the rule, where
        # nothing can overwrite it.
        config = event_rule.action_data or {}
        name = config.get('check')
        if not name:
            logger.warning('Event rule "%s" reports no check name; nothing to do.', event_rule)
            return

        change_request = self._change_request(event_context)
        if change_request is None:
            # The ordinary case, not a fault: an event rule fires for changes made on main as
            # well as in a branch, and a change on main has no change request to report to.
            logger.debug('Event rule "%s" fired outside a branch with a change request.', event_rule)
            return

        row = MergeCheck.objects.filter(change_request=change_request, name=name).first()
        if row is None:
            logger.warning(
                'Event rule "%s" reports check "%s", which %s does not have. Name it in a '
                "policy's Checks field, or in required_external_checks, so the check exists.",
                event_rule,
                name,
                change_request,
            )
            return

        status = config.get('status', MergeCheckStatusChoices.FAILURE)
        # Cut to fit rather than refused: losing the tail of a message is better than losing
        # the result it came with.
        summary = (config.get('summary') or self._default_summary(event_rule, event_context))[: _max_length('summary')]

        # `validate()` rejects an over-long URL on the form, so reaching this with one means
        # the rule was written straight to the database. Drop it rather than cut it: a cut URL
        # points somewhere else. Dropping it costs a link and keeps the result, and letting it
        # through would raise a DataError inside the event pipeline instead.
        details_url = config.get('details_url') or ''
        if len(details_url) > _max_length('details_url'):
            logger.warning(
                'Event rule "%s" has a details_url longer than %s characters; reporting %s without it.',
                event_rule,
                _max_length('details_url'),
                name,
            )
            details_url = ''

        if (row.status, row.summary, row.details_url) == (status, summary, details_url):
            # A rule which fires twice on the same answer is not a change. Writing it anyway
            # would fill the changelog with noise and bury the transitions worth reading,
            # which is the same rule `run_checks` follows.
            return

        row.status = status
        row.summary = summary
        row.details_url = details_url
        row.completed = timezone.now()
        # save(), not queryset.update(): the post_save receiver is what refreshes the cached
        # readiness and lets a passing result release an automatic merge, and update() fires
        # none. It is also what puts the result in the changelog.
        row.save(update_fields=['status', 'summary', 'details_url', 'completed'])

        logger.info('Event rule "%s" reported %s as %s on %s.', event_rule, name, status, change_request)

    @staticmethod
    def _change_request(event_context):
        """
        The change request this event belongs to, or None.

        Two ways in, and the order matters. An event about a change request names one
        outright, which is what a rule on this plugin's own lifecycle events carries. Anything
        else is a change to an ordinary object, and belongs to a change request only through
        the branch it was made in.

        The branch is read off the request rather than out of `active_branch`. Both are set
        for the duration of a request, but they are torn down in opposite order: branching
        registers its context manager after NetBox registers event tracking, so it exits
        first, and the events are flushed on the way out of the tracking that outlives it. By
        the time an action runs, the contextvar is already back to None. The attribute
        branching's middleware leaves on the request is not, so that is what is read; the
        contextvar is kept only as a fallback for a caller which has no request at all, such
        as a script running inside `activate_branch`.
        """
        from netbox_branching.contextvars import active_branch

        from netbox_change_control.models import ChangeRequest

        obj = event_context.get('object')
        if isinstance(obj, ChangeRequest):
            return obj

        request = event_context.get('request')
        branch = getattr(request, 'active_branch', None) or active_branch.get()
        if branch is None:
            return None

        return ChangeRequest.objects.filter(branch=branch).first()

    @staticmethod
    def _default_summary(event_rule, event_context):
        """
        What the check says when the rule does not say anything.

        A result with no summary is a red badge and no explanation, in the one place somebody
        is deciding whether to allow a change. Naming the rule and the object which tripped it
        turns that into something a reviewer can act on without going to look.
        """
        display = ''
        data = event_context.get('data')
        if isinstance(data, dict):
            display = data.get('display') or ''

        summary = f'Reported by event rule "{event_rule}"'
        return f'{summary}: {display}' if display else summary


# NetBox reads this from PluginConfig.ready(), by the default resource path for the
# `event_rule_actions` resource. Adding an action here is all it takes to offer another.
event_rule_actions = (ReportCheckAction,)
