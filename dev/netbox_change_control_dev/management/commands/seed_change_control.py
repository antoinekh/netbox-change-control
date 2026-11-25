"""
Build a complete change-control scenario in one command.

Creates reviewer groups and users, two policies of different strictness, a branch holding
real changes, and an open change request. The result is a system you can click through
immediately.
"""

import uuid
from dataclasses import dataclass, field
from itertools import chain

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from netbox_change_control_dev.inventory import ensure_demo_inventory

DEMO_PASSWORD = 'demo1234'

REVIEWERS = {
    'Change Engineers': ['erin', 'frank', 'grace'],
    'Change Leads': ['helen', 'ivan'],
}


@dataclass
class RuleSpec:
    name: str
    min_reviews: int
    groups: list


@dataclass
class PolicySpec:
    name: str
    description: str
    weight: int
    object_types: list
    rules: list
    enabled: bool = True
    #: Pre-merge checks this policy requires. Built-in checks are only registered, never
    #: applied, until a policy names them.
    checks: list = field(default_factory=list)
    #: An optional condition set narrowing the policy on the values of the changed objects.
    conditions: dict = None
    #: Which side of the change the conditions are read against: either, after or before.
    condition_state: str = 'either'


class Command(BaseCommand):
    help = 'Seed demo policies, reviewers, a branch and an open change request.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--branch-name',
            default='demo-change',
            help='Name of the branch to create (default: demo-change).',
        )
        parser.add_argument(
            '--skip-branch',
            action='store_true',
            help='Create only the policies and reviewers, no branch and no change request.',
        )
        parser.add_argument(
            '--adopt',
            nargs='*',
            default=None,
            metavar='BRANCH',
            help=(
                'Open a change request for each named existing branch. Pass with no names to '
                'adopt every ready branch which has changes but no change request yet.'
            ),
        )

    def handle(self, *args, **options):
        from core.models import ObjectType
        from users.models import Group, User

        from netbox_change_control.models import Policy, PolicyRule

        with transaction.atomic():
            for name in ensure_demo_inventory():
                self.stdout.write(f'  created {name} on main')
            groups = self._create_reviewers(Group, User)
            self._create_policies(Policy, PolicyRule, ObjectType, groups)
            self._grant_permissions(groups)

        if options['adopt'] is not None:
            self._adopt_branches(options['adopt'])

        if options['skip_branch']:
            self.stdout.write(self.style.SUCCESS('Policies and reviewers created.'))
            return

        self._create_branch_and_request(options['branch_name'])

    def _create_reviewers(self, Group, User):
        groups = {}
        for group_name, usernames in REVIEWERS.items():
            group, _ = Group.objects.get_or_create(name=group_name)
            groups[group_name] = group
            for username in usernames:
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        'email': f'{username}@example.com',
                        'password': make_password(DEMO_PASSWORD),
                    },
                )
                if created:
                    self.stdout.write(f'  created user {username} (password: {DEMO_PASSWORD})')
                user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f'Reviewer groups ready: {", ".join(groups)}'))
        return groups

    def _create_policies(self, Policy, PolicyRule, ObjectType, groups):
        """
        Create a graded set of policies, from a catch-all baseline to tightly scoped rules.
        Together they show every knob: unscoped, scoped by object type, multi-rule, and a
        disabled policy.
        """
        engineers = groups['Change Engineers']
        leads = groups['Change Leads']

        specs = [
            PolicySpec(
                name='Baseline review',
                description='Every change needs one engineer to approve, and passes the standard checks.',
                weight=100,
                object_types=[],
                rules=[RuleSpec('One engineer', 1, [engineers])],
                # No object types, so this matches every branch. That is how a check is made
                # to apply everywhere now that built-ins are chosen per policy.
                checks=['has-changes', 'no-conflicts', 'not-stale'],
            ),
            PolicySpec(
                name='Device changes',
                description='Touching devices or interfaces needs two engineers and one lead.',
                weight=200,
                object_types=[('dcim', 'device'), ('dcim', 'interface')],
                rules=[
                    RuleSpec('Two engineers', 2, [engineers]),
                    RuleSpec('One lead', 1, [leads]),
                ],
                # Device work draws the most discussion, so every thread must be settled.
                checks=['threads-resolved'],
            ),
            PolicySpec(
                name='Circuit changes',
                description='Circuits are customer facing, so a lead must sign off.',
                weight=200,
                # Live circuits only. Read on either side, so switching one off is caught as
                # well as switching one on.
                conditions={'attr': 'status', 'value': 'active'},
                condition_state='either',
                object_types=[
                    ('circuits', 'circuit'),
                    ('circuits', 'circuittermination'),
                    ('circuits', 'provider'),
                ],
                rules=[
                    RuleSpec('One engineer', 1, [engineers]),
                    RuleSpec('One lead', 1, [leads]),
                ],
                checks=['threads-resolved'],
            ),
            PolicySpec(
                name='IPAM changes',
                description='Prefix and IP address edits need a single engineer review.',
                weight=300,
                object_types=[('ipam', 'prefix'), ('ipam', 'ipaddress'), ('ipam', 'vlan')],
                rules=[RuleSpec('One engineer', 1, [engineers])],
            ),
            PolicySpec(
                name='Site and rack changes',
                description='Physical estate changes need two leads. Disabled by default.',
                weight=400,
                object_types=[('dcim', 'site'), ('dcim', 'rack'), ('dcim', 'location')],
                rules=[RuleSpec('Two leads', 2, [leads])],
                enabled=False,
            ),
        ]

        for spec in specs:
            policy, created = Policy.objects.get_or_create(
                name=spec.name,
                defaults={
                    'description': spec.description,
                    'weight': spec.weight,
                    'enabled': spec.enabled,
                },
            )
            policy.checks = spec.checks
            policy.conditions = spec.conditions
            policy.condition_state = spec.condition_state
            policy.save(update_fields=['checks', 'conditions', 'condition_state'])
            if spec.object_types:
                query = None
                for app_label, model in spec.object_types:
                    clause = Q(app_label=app_label, model=model)
                    query = clause if query is None else query | clause
                policy.object_types.set(ObjectType.objects.filter(query))
            for rule_spec in spec.rules:
                rule, _ = PolicyRule.objects.get_or_create(
                    policy=policy,
                    name=rule_spec.name,
                    defaults={'min_reviews': rule_spec.min_reviews},
                )
                rule.groups.set(rule_spec.groups)
            state = 'created' if created else 'existing'
            scope = ', '.join(f'{a}.{m}' for a, m in spec.object_types) or 'all object types'
            checks = ', '.join(spec.checks) or 'none'
            self.stdout.write(f'  policy "{policy.name}" ({state}) scope: {scope}; checks: {checks}')
            if spec.conditions:
                self.stdout.write(f'    conditions: {spec.conditions} ({spec.condition_state})')

    def _create_branch_and_request(self, branch_name):
        from netbox_branching.models import Branch
        from users.models import User

        from netbox_change_control.models import ChangeRequest

        admin = User.objects.filter(is_superuser=True).order_by('pk').first()
        if admin is None:
            self.stdout.write(self.style.ERROR('No superuser found; cannot own the branch.'))
            return

        if Branch.objects.filter(name=branch_name).exists():
            self.stdout.write(self.style.WARNING(f'Branch "{branch_name}" already exists; skipping.'))
            return

        self.stdout.write(f'Provisioning branch "{branch_name}". This takes a moment.')
        branch = Branch(name=branch_name, owner=admin, description='Seeded by seed_change_control')
        branch.save(provision=False)
        branch.provision(user=admin)
        branch.refresh_from_db()

        self._make_changes_in(branch)

        change_request = ChangeRequest.objects.create(
            branch=branch,
            ref='CHG0012345',
            title='Rename a demo device',
            description='Seeded change request used to exercise the review workflow.',
            requester=admin,
        )
        # Submitting is what leaves draft, matches the policies and tells the reviewers.
        change_request.submit()

        self.stdout.write(
            self.style.SUCCESS(
                f'Branch "{branch.name}" and {change_request} created. '
                f'Log in as one of {", ".join(chain.from_iterable(REVIEWERS.values()))} '
                f'(password: {DEMO_PASSWORD}) to review it.'
            )
        )

    def _make_changes_in(self, branch):
        """
        Make a real edit inside the branch so the ChangeDiff table has content for the
        policy matcher to read.

        NetBox only writes ObjectChange records inside a request context, so the edit is
        wrapped in `event_tracking` with a synthetic request. Without it the branch records
        no changes at all and every scoped policy silently fails to match.
        """
        from dcim.models import Device
        from django.test import RequestFactory
        from netbox.context_managers import event_tracking
        from netbox_branching.contextvars import active_branch
        from users.models import User

        admin = User.objects.filter(is_superuser=True).order_by('pk').first()
        request = RequestFactory().post('/')
        request.id = uuid.uuid4()
        request.user = admin

        token = active_branch.set(branch)
        try:
            with event_tracking(request):
                device = Device.objects.using(branch.connection_name).order_by('pk').first()
                if device is None:
                    self.stdout.write(self.style.WARNING('No devices found; branch has no changes.'))
                    return
                # Snapshot first, or NetBox records no prechange data and branching stores
                # the diff with original=None, which stops it ever detecting a conflict.
                device.snapshot()
                device.description = 'Touched by the change-control demo seed'
                device.save(using=branch.connection_name)
                self.stdout.write(f'  edited device "{device}" inside the branch')
        finally:
            active_branch.reset(token)

        branch.refresh_from_db()
        self.stdout.write(f'  branch records {branch.get_changes().count()} change(s)')

    def _adopt_branches(self, names):
        """
        Open a change request for branches which already exist, for example one a user
        created by hand in the UI.
        """
        from netbox_branching.choices import BranchStatusChoices
        from netbox_branching.models import Branch, ChangeDiff
        from users.models import User

        from netbox_change_control.models import ChangeRequest

        admin = User.objects.filter(is_superuser=True).order_by('pk').first()

        if names:
            branches = Branch.objects.filter(name__in=names)
            missing = set(names) - set(branches.values_list('name', flat=True))
            for name in sorted(missing):
                self.stdout.write(self.style.WARNING(f'No branch named "{name}".'))
        else:
            branches = Branch.objects.filter(status=BranchStatusChoices.READY)

        for branch in branches:
            if ChangeRequest.objects.filter(branch=branch).exists():
                self.stdout.write(f'  branch "{branch.name}" already has a change request')
                continue
            if not ChangeDiff.objects.filter(branch=branch).exists():
                self.stdout.write(f'  branch "{branch.name}" has no changes; skipped')
                continue

            change_request = ChangeRequest.objects.create(
                branch=branch,
                title=f'Merge {branch.name} into main',
                description=branch.description or f'Review the changes staged in {branch.name}.',
                requester=branch.owner or admin,
            )
            change_request.submit()
            bindings = change_request.policy_bindings.all()
            names_applied = ', '.join(b.policy.name for b in bindings) or 'none'
            self.stdout.write(
                self.style.SUCCESS(f'  {change_request} opened for "{branch.name}"; policies: {names_applied}')
            )

    def _grant_permissions(self, groups):
        """
        Give the reviewer groups enough access to be useful.

        A NetBox user with no object permissions can log in but sees an empty application,
        so seeded reviewers would be unable to open, let alone review, anything.
        """
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission

        grants = [
            (
                'Change control: review',
                ['view', 'add', 'change'],
                [
                    ('netbox_change_control', 'changerequest'),
                    ('netbox_change_control', 'review'),
                    ('netbox_change_control', 'changecomment'),
                ],
            ),
            (
                'Change control: read policies',
                ['view'],
                [
                    ('netbox_change_control', 'policy'),
                    ('netbox_change_control', 'policyrule'),
                    ('netbox_change_control', 'mergecheck'),
                ],
            ),
            (
                'Change control: read branches',
                ['view'],
                [
                    ('netbox_branching', 'branch'),
                    ('netbox_branching', 'changediff'),
                ],
            ),
            (
                # Write access so protect_main can actually be demonstrated: an engineer who
                # may edit a circuit is still refused on main while the scope covers it.
                'Change control: edit circuits',
                ['view', 'add', 'change', 'delete'],
                [
                    ('circuits', 'circuit'),
                    ('circuits', 'circuittermination'),
                    ('circuits', 'provider'),
                    ('circuits', 'circuittype'),
                ],
            ),
            (
                'Change control: read inventory',
                ['view'],
                [
                    ('dcim', 'device'),
                    ('dcim', 'interface'),
                    ('dcim', 'site'),
                    ('dcim', 'rack'),
                    ('circuits', 'circuit'),
                    ('circuits', 'circuittermination'),
                    ('circuits', 'provider'),
                    ('ipam', 'prefix'),
                    ('ipam', 'ipaddress'),
                    ('ipam', 'vlan'),
                ],
            ),
        ]

        for name, actions, models in grants:
            permission, _ = ObjectPermission.objects.get_or_create(
                name=name,
                defaults={'actions': actions, 'enabled': True},
            )
            permission.actions = actions
            permission.save()

            content_types = [
                ct
                for ct in (ContentType.objects.filter(app_label=app, model=model).first() for app, model in models)
                if ct is not None
            ]
            permission.object_types.set(content_types)
            permission.groups.set(groups.values())

        self.stdout.write(self.style.SUCCESS(f'Granted {len(grants)} permission sets to the reviewer groups.'))
