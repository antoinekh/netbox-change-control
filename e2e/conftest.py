"""
Browser tests against a running NetBox with the plugin installed.

They prepare their data through the REST API and act through the browser as three people: a
requester, who opens and merges the change, a reviewer, who approves it, and a viewer, who may
only read it. Every object they create is deleted at the end, and their policy covers only site
groups, so the data of the NetBox they run against is left alone. A NetBox whose own policies
cover every branch still changes what they see, so they run on a NetBox of their own.

`make e2e` starts one from dev/stack/ on its own database, and runs them. Against another
NetBox, with its background worker running, since jobs provision and merge the branches:

    NETBOX_URL=http://localhost:8891 E2E_AUTHORIZATION='Bearer nbt_...' \\
        uv run --locked --only-group e2e pytest e2e

E2E_AUTHORIZATION is the whole Authorization header of a superuser's API token.
"""

import os
import time
import uuid
from dataclasses import dataclass, field

import pytest

PASSWORD = 'e2e-Passw0rd!'
OBJECT_TYPES = [
    'netbox_change_control.changerequest',
    'netbox_change_control.review',
    'netbox_change_control.changecomment',
    'netbox_change_control.mergecheck',
    'netbox_change_control.policy',
    'netbox_change_control.policyrule',
    'netbox_branching.branch',
    'netbox_branching.changediff',
    'dcim.sitegroup',
    'core.job',
]
ACTIONS = ['view', 'add', 'change', 'delete', 'merge', 'abandon', 'reopen']
CHECK = 'e2e-ci'


def pytest_configure(config):
    if not os.environ.get('E2E_AUTHORIZATION'):
        raise pytest.UsageError('Set E2E_AUTHORIZATION to the Authorization header of a superuser API token.')


@pytest.fixture(scope='session')
def base_url():
    return os.environ.get('NETBOX_URL', 'http://localhost:8891').rstrip('/')


@dataclass
class Api:
    """
    The REST API as the token's superuser, remembering what it creates so it can remove it.
    """

    request: object
    created: list = field(default_factory=list)

    def call(self, method, path, data=None, branch=None, expect=(200, 201, 204)):
        headers = {'X-NetBox-Branch': branch['schema_id']} if branch else {}
        response = self.request.fetch(f'/api/{path}', method=method, data=data, headers=headers)
        assert response.status in expect, f'{method} {path}: {response.status} {response.text()}'
        return response.json() if response.status != 204 else None

    def create(self, path, data, branch=None):
        obj = self.call('POST', path, data, branch=branch)
        if branch is None:
            self.created.append(f'{path}{obj["id"]}/')
        return obj

    def cleanup(self):
        for path in reversed(self.created):
            self.call('DELETE', path, expect=(204, 404))


@pytest.fixture(scope='session')
def api(playwright, base_url):
    context = playwright.request.new_context(
        base_url=base_url,
        extra_http_headers={'Authorization': os.environ['E2E_AUTHORIZATION'], 'Accept': 'application/json'},
    )
    api = Api(context)
    yield api
    api.cleanup()
    context.dispose()


@dataclass(frozen=True)
class People:
    requester: str
    reviewer: str
    viewer: str
    policy: int
    # The reported check the e2e policy requires.
    check: str = CHECK


@pytest.fixture(scope='session')
def people(api):
    """
    A requester, a reviewer and a viewer, and a policy needing one approval and one reported check.
    """
    suffix = uuid.uuid4().hex[:6]
    group = api.create('users/groups/', {'name': f'e2e-reviewers-{suffix}'})
    requester = api.create('users/users/', {'username': f'e2e-requester-{suffix}', 'password': PASSWORD})
    reviewer = api.create(
        'users/users/', {'username': f'e2e-reviewer-{suffix}', 'password': PASSWORD, 'groups': [group['id']]}
    )
    viewer = api.create('users/users/', {'username': f'e2e-viewer-{suffix}', 'password': PASSWORD})
    api.create(
        'users/permissions/',
        {
            'name': f'e2e-{suffix}',
            'object_types': OBJECT_TYPES,
            'actions': ACTIONS,
            'users': [requester['id'], reviewer['id']],
        },
    )
    api.create(
        'users/permissions/',
        {'name': f'e2e-view-{suffix}', 'object_types': OBJECT_TYPES, 'actions': ['view'], 'users': [viewer['id']]},
    )
    site_group = api.call('GET', 'core/object-types/?app_label=dcim&model=sitegroup')['results'][0]
    policy = api.create(
        'plugins/change-control/policies/',
        {'name': f'e2e site groups {suffix}', 'object_types': [site_group['id']], 'checks': [CHECK]},
    )
    api.create(
        'plugins/change-control/policy-rules/',
        {'policy': policy['id'], 'name': 'One reviewer', 'min_reviews': 1, 'groups': [group['id']]},
    )
    return People(
        requester=requester['username'], reviewer=reviewer['username'], viewer=viewer['username'], policy=policy['id']
    )


@pytest.fixture
def branch(api):
    """
    A provisioned branch holding one new site group, which is what the e2e policy covers.
    """
    name = f'e2e-{uuid.uuid4().hex[:8]}'
    branch = api.create('plugins/branching/branches/', {'name': name})
    for _ in range(60):
        branch = api.call('GET', f'plugins/branching/branches/{branch["id"]}/')
        if branch['status']['value'] == 'ready':
            break
        time.sleep(1)
    else:
        pytest.fail(f'Branch {name} was not provisioned; is the worker running?')
    api.call('POST', 'dcim/site-groups/', {'name': name, 'slug': name}, branch=branch)
    return branch


@pytest.fixture
def login(browser, base_url):
    """
    Open a browser session signed in as a user, one context per person.
    """
    contexts = []

    def login(username):
        context = browser.new_context(base_url=base_url)
        contexts.append(context)
        page = context.new_page()
        page.goto('/login/')
        page.fill('input[name=username]', username)
        page.fill('input[name=password]', PASSWORD)
        page.click('button[type=submit]')
        page.wait_for_url(lambda url: '/login/' not in url)
        page.wait_for_load_state()
        return page

    yield login
    for context in contexts:
        context.close()
