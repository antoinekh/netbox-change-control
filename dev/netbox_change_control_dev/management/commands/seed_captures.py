"""
Build the states the documentation screenshots need.

Every screenshot in the documentation needs the application in one particular state: a request
blocked by a failing check, one ready to merge, one in conflict with main, a branch nobody has
opened a request against. Reproducing those by hand takes long enough that a screenshot rarely
gets retaken, so this builds all of them in one go and prints where to point the camera.

The SHOTS list below is the record of which file goes with which page and what it should show.

Each run tears its own branches down first, so a capture session always starts from the same
picture. Nothing outside the `capture-` branches is touched.
"""

import uuid
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand

from netbox_change_control_dev import BASE_URL
from netbox_change_control_dev.inventory import ensure_demo_inventory

BRANCH_PREFIX = 'capture-'

#
# Scenarios
#


@dataclass
class Scenario:
    slug: str
    #: External reference, as a real request would carry: a ticket id or a change number.
    ref: str
    title: str
    description: str
    #: Usernames whose approval to record, in order.
    approvers: list = field(default_factory=list)
    #: Leave an unresolved comment thread, which fails the threads-resolved check.
    open_thread: bool = False
    #: Edit the same object on main afterwards, so the branch genuinely conflicts.
    conflict: bool = False
    #: Leave the branch without a change request at all, which is the other half of what the
    #: branch page has to say: it cannot merge, and here is the button that fixes it.
    no_request: bool = False

    @property
    def branch_name(self):
        return f'{BRANCH_PREFIX}{self.slug}'


SCENARIOS = [
    Scenario(
        slug='ready',
        ref='CHG0012346',
        title='Upgrade the access switch firmware',
        description='Fully approved, every check passing. Used for the active merge button.',
        approvers=['erin', 'frank', 'helen'],
    ),
    Scenario(
        slug='blocked',
        ref='CHG0012351',
        title='Retire the spare access switch',
        description='Approved by a lead only, with a comment thread still open.',
        approvers=['helen'],
        open_thread=True,
    ),
    Scenario(
        slug='unmanaged',
        ref='',
        title='',
        description='A ready branch nobody has opened a change request against.',
        no_request=True,
    ),
    Scenario(
        slug='conflict',
        ref='INC-4471',
        title='Re-describe the core switch',
        description='Main has moved the same field, so the branch really conflicts.',
        conflict=True,
    ),
]

#
# What to capture once the states exist
#


@dataclass
class Shot:
    file: str
    page: str
    path: str
    user: str
    what: str


SHOTS = [
    Shot(
        file='merge-checks.png',
        page='docs/checks.md',
        path='{blocked}',
        user='admin',
        what='The Pre-merge checks panel, cropped. Three passing, threads-resolved failing, '
        'with the Re-run checks button.',
    ),
    Shot(
        file='merge-button.png',
        page='docs/merging.md',
        path='{ready}',
        user='admin',
        what='The approval status card footer with an active Merge branch button.',
    ),
    Shot(
        file='change-request-detail.png',
        page='README.md',
        path='{conflict}',
        user='admin',
        what='The whole page: the conflict banner, the approval status with each rule and who '
        'may approve it, the checks, and the disabled merge button with its reason.',
    ),
    Shot(
        file='change-request-list.png',
        page='docs/change-requests.md',
        path='/plugins/change-control/change-requests/',
        user='admin',
        what='The whole table. Four requests, each with a reference: one approved and ready, '
        'one blocked by an open thread, one in conflict, one plain.',
    ),
    Shot(
        file='policy-detail.png',
        page='docs/policies.md',
        path='{policy}',
        user='admin',
        what='The left column. Conditions, and the line below stating which side of a change they are read against.',
    ),
    Shot(
        file='my-reviews-widget.png',
        page='docs/automation.md',
        path='/',
        user='erin',
        what='The My Reviews widget, already placed on the dashboard by this command. It lists '
        'the blocked request, which is waiting on an engineer.',
    ),
    Shot(
        file='capture-blocked.png',
        page='docs/merging.md',
        path='{blocked_branch}',
        user='admin',
        what="The card this plugin injects into the right-hand column of netbox-branching's own branch page, "
        'in context. It carries the status badge, the reference, the title, each rule still '
        'short with who may satisfy it, and the Open change request button.',
    ),
    Shot(
        file='capture-unmanaged.png',
        page='docs/merging.md',
        path='{unmanaged_branch}',
        user='admin',
        what='The same place on a branch nobody has opened a request against: the warning that '
        'it cannot merge, and the Open a change request button.',
    ),
    Shot(
        file='capture-ready.png',
        page='docs/merging.md',
        path='{ready_branch}',
        user='admin',
        what='The same card in its satisfied state, green, reading "Every gate is satisfied".',
    ),
    Shot(
        file='protect-main-blocked.png',
        page='docs/protect-main.md',
        path='{provider}',
        user='erin',
        what='Change the description and save. protect_main refuses the write because the '
        'scope covers circuits and Erin is not a superuser.',
    ),
]


class Command(BaseCommand):
    help = 'Build the change-request states the documentation screenshots need.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=[s.slug for s in SCENARIOS],
            help='Rebuild a single scenario rather than all of them.',
        )
        parser.add_argument(
            '--teardown',
            action='store_true',
            help='Remove the capture branches and their change requests, then stop.',
        )

    def handle(self, *args, **options):
        scenarios = [s for s in SCENARIOS if not options['only'] or s.slug == options['only']]

        if options['teardown']:
            self._teardown(scenarios)
            self.stdout.write(self.style.SUCCESS('Capture scenarios removed.'))
            return

        if not self._check_seed():
            return

        for name in ensure_demo_inventory():
            self.stdout.write(f'  created {name} on main')

        self._teardown(scenarios)

        built = {}
        for scenario in scenarios:
            result = self._build(scenario)
            if result is not None:
                built[scenario.slug] = result

        self._place_widget()
        self._report(built)

    #
    # Preconditions
    #

    def _check_seed(self):
        """
        The reviewers and their permissions come from seed_change_control. Without them the
        approvals below have nobody to attribute, and Erin cannot open anything.
        """
        from users.models import User

        missing = [name for name in ('erin', 'frank', 'helen') if not User.objects.filter(username=name).exists()]
        if missing:
            self.stdout.write(self.style.ERROR(f'Missing seeded users: {", ".join(missing)}. Run "make seed" first.'))
            return False
        if self._admin() is None:
            self.stdout.write(self.style.ERROR('No superuser found; one is needed to own the branches.'))
            return False
        return True

    def _admin(self):
        from users.models import User

        return User.objects.filter(is_superuser=True).order_by('pk').first()

    def _user(self, username):
        from users.models import User

        return User.objects.get(username=username)

    #
    # Building
    #

    def _teardown(self, scenarios):
        from netbox_branching.models import Branch

        from netbox_change_control.models import ChangeRequest

        names = [s.branch_name for s in scenarios]
        ChangeRequest.objects.filter(branch_name__in=names, branch__isnull=True).delete()
        for branch in Branch.objects.filter(name__in=names):
            self.stdout.write(f'  removing branch "{branch.name}"')
            # Deleting the branch clears the change request's FK; delete the record too, or
            # the next run leaves an orphan behind on every page that lists requests.
            ChangeRequest.objects.filter(branch=branch).delete()
            branch.delete()

        # The conflict scenario writes to main, which is outside any branch. Put it back, so
        # a teardown leaves the database as it found it.
        for scenario in scenarios:
            if not scenario.conflict:
                continue
            device = self._pick_device(scenario)
            if device is not None:
                device.refresh_from_db()
                self._reset_on_main(device)

    def _build(self, scenario):
        from netbox_change_control.checks import run_checks
        from netbox_change_control.models import ChangeRequest

        self.stdout.write(self.style.MIGRATE_HEADING(f'\nScenario "{scenario.slug}": {scenario.description}'))

        device = self._pick_device(scenario)
        if device is None:
            return None

        # Reset before the branch is taken. The conflict scenario leaves a value on main, and
        # without this a second run would branch from that value, make the same edit again,
        # record no ObjectChange, and quietly produce no conflict at all.
        self._reset_on_main(device)

        branch = self._provision(scenario.branch_name)
        self._edit_in_branch(branch, device)

        if scenario.no_request:
            self.stdout.write('  left without a change request, on purpose')
            return branch

        change_request = ChangeRequest.objects.create(
            branch=branch,
            ref=scenario.ref,
            title=scenario.title,
            description=scenario.description,
            requester=self._admin(),
        )
        # Submitting is what leaves draft: it matches the policies and tells the reviewers.
        # A review against a draft moves nothing, so it has to come before the approvals.
        change_request.submit()
        applied = change_request.policy_bindings.all()
        self.stdout.write(f'  policies applied: {", ".join(b.policy.name for b in applied) or "none"}')

        if scenario.open_thread:
            self._add_open_thread(change_request)

        for username in scenario.approvers:
            self._approve(change_request, username)

        if scenario.conflict:
            self._move_main(device)

        run_checks(change_request)
        change_request.refresh_from_db()
        self._describe(change_request)
        return change_request

    def _provision(self, name):
        from netbox_branching.models import Branch

        self.stdout.write(f'  provisioning branch "{name}"')
        branch = Branch(name=name, owner=self._admin(), description='Built by seed_captures')
        branch.save(provision=False)
        branch.provision(user=self._admin())
        branch.refresh_from_db()
        return branch

    def _pick_device(self, scenario):
        """
        One device per scenario. The conflict scenario moves its device on main, and sharing
        a device would drag the other scenarios into that conflict.
        """
        from dcim.models import Device

        offset = SCENARIOS.index(scenario)
        device = Device.objects.order_by('pk')[offset : offset + 1].first()
        if device is None:
            self.stdout.write(self.style.WARNING('  no devices in the database; load the demo data first'))
        return device

    def _reset_on_main(self, device):
        """
        Put the device description back to empty, so every run starts from the same baseline.
        """
        from dcim.models import Device

        if not device.description:
            return
        self._write_on_main(Device, device.pk, '')
        self.stdout.write(f'  reset device "{device}" on main')

    def _edit_in_branch(self, branch, device):
        """
        Edit the device inside the branch, so the diff has content.

        NetBox writes ObjectChange records only inside a request context, so the edit runs
        under `event_tracking` with a synthetic request. Without it the branch records no
        changes at all and every scoped policy silently fails to match.
        """
        from dcim.models import Device
        from django.test import RequestFactory
        from netbox.context_managers import event_tracking
        from netbox_branching.contextvars import active_branch

        request = RequestFactory().post('/')
        request.id = uuid.uuid4()
        request.user = self._admin()

        token = active_branch.set(branch)
        try:
            with event_tracking(request):
                in_branch = Device.objects.using(branch.connection_name).get(pk=device.pk)
                # Without a snapshot NetBox records no prechange data, branching stores the
                # diff with original=None, and _update_conflicts returns before comparing
                # anything. The branch could then never report a conflict.
                in_branch.snapshot()
                in_branch.description = f'Staged in {branch.name}'
                in_branch.save(using=branch.connection_name)
        finally:
            active_branch.reset(token)

        self.stdout.write(f'  edited device "{device}" inside the branch')

    def _move_main(self, device):
        """
        Change the same field on main, which is what makes the branch conflict. Branching
        updates the "current" side of the existing diff.
        """
        from dcim.models import Device

        # Branching recomputes the diff's conflicts from this write, which is what makes the
        # scenario conflicted. On 1.1.2 and earlier it did not, and the diffs had to be
        # re-saved by hand here; that workaround went with the 1.1.3 requirement.
        self._write_on_main(Device, device.pk, 'Changed on main after the branch was taken')
        self.stdout.write(f'  moved device "{device}" on main, so the branch now conflicts')

    def _write_on_main(self, model, pk, description):
        """
        Set a description on main, recorded the way the interface would record it.

        Snapshot first, or NetBox writes no prechange data, branching stores the diff with
        original=None, and `_update_conflicts` returns before comparing anything. The branch
        could then never report a conflict at all.
        """
        from django.test import RequestFactory
        from netbox.context_managers import event_tracking

        request = RequestFactory().post('/')
        request.id = uuid.uuid4()
        request.user = self._admin()

        with event_tracking(request):
            obj = model.objects.get(pk=pk)
            obj.snapshot()
            obj.description = description
            obj.save()

    def _add_open_thread(self, change_request):
        from netbox_branching.models import ChangeDiff

        from netbox_change_control.models import ChangeComment

        diff = ChangeDiff.objects.filter(branch=change_request.branch).order_by('pk').first()
        if diff is None:
            self.stdout.write(self.style.WARNING('  no diff to comment on'))
            return

        ChangeComment.objects.create(
            change_request=change_request,
            change_diff=diff,
            author=self._user('helen'),
            text='Is this the right device? Please confirm before we merge.',
        )
        self.stdout.write('  left one comment thread open')

    def _approve(self, change_request, username):
        from netbox_change_control.choices import ReviewDecisionChoices
        from netbox_change_control.models import Review

        Review.objects.create(
            change_request=change_request,
            reviewer=self._user(username),
            decision=ReviewDecisionChoices.APPROVE,
            comment=f'Looks good to me.\n\nChecked by **{username}**.',
        )
        self.stdout.write(f'  approved by {username}')

    def _place_widget(self):
        """
        Put the My Reviews widget on Erin's dashboard, so the screenshot does not depend on
        somebody adding it by hand first.
        """
        from extras.models import Dashboard

        from netbox_change_control.dashboard import MyReviewsWidget

        erin = self._user('erin')
        dashboard, _ = Dashboard.objects.get_or_create(user=erin)
        dashboard.layout = []
        dashboard.config = {}
        # Dashboard.config is a JSON column, and default_title is a lazy translation proxy,
        # which json.dumps refuses. Resolve it to a plain string first.
        widget = MyReviewsWidget(title=str(MyReviewsWidget.default_title), width=6, height=4)
        dashboard.add_widget(widget, x=0, y=0)
        dashboard.save()
        self.stdout.write(self.style.SUCCESS("\nPlaced the My Reviews widget on Erin's dashboard."))

    #
    # Reporting
    #

    def _describe(self, change_request):
        checks = ', '.join(f'{c.name}={c.status}' for c in change_request.checks.all())
        self.stdout.write(f'  status: {change_request.status}')
        self.stdout.write(f'  checks: {checks or "none"}')
        self.stdout.write(f'  conflicts: {len(change_request.conflicts)}')
        ready = change_request.is_ready_to_merge
        reason = change_request.merge_blocked_reason
        self.stdout.write(f'  ready to merge: {ready}{"" if ready else f" ({reason})"}')

    def _report(self, built):
        from circuits.models import Provider
        from netbox_branching.models import Branch

        from netbox_change_control.models import ChangeRequest, Policy

        provider = Provider.objects.order_by('pk').first()
        # A scenario yields a change request, except the one that deliberately has none and
        # yields its branch instead.
        paths = {slug: obj.get_absolute_url() for slug, obj in built.items() if isinstance(obj, ChangeRequest)}
        # The branch pages, for the alert this plugin injects into them.
        for scenario in SCENARIOS:
            branch = Branch.objects.filter(name=scenario.branch_name).first()
            if branch is not None:
                paths[f'{scenario.slug}_branch'] = branch.get_absolute_url()
        paths['provider'] = f'/circuits/providers/{provider.pk}/edit/' if provider else '(no provider in the database)'

        # The condition-state line only renders on a policy which has conditions.
        policy = Policy.objects.exclude(conditions=None).order_by('pk').first()
        paths['policy'] = policy.get_absolute_url() if policy else '(no policy carries conditions)'

        self.stdout.write(self.style.MIGRATE_HEADING('\nShots to take'))
        for shot in SHOTS:
            try:
                path = shot.path.format(**paths)
            except KeyError:
                continue
            self.stdout.write(f'\n  {self.style.SUCCESS(shot.file)}  ->  {shot.page}')
            self.stdout.write(f'    sign in as : {shot.user}')
            self.stdout.write(f'    open       : {BASE_URL}{path}')
            self.stdout.write(f'    capture    : {shot.what}')

        self.stdout.write(
            '\nSeeded reviewers use the password demo1234. Use a private window to hold two sessions at once.'
        )
