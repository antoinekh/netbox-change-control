"""
What a requester and a reviewer do in the interface, from opening a change to its record.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from playwright.sync_api import expect


def open_request(page, api, branch, **fields):
    """
    Open a change request for the branch through the add form, and return its URL.
    """
    query = urlencode({'branch': branch['id'], 'title': branch['name'], **fields})
    page.goto(f'/plugins/change-control/change-requests/add/?{query}')
    page.click('button[name=_create]')
    page.wait_for_url(re.compile(r'/change-requests/\d+/$'))
    request_id = page.url.rstrip('/').rsplit('/', 1)[1]
    api.created.append(f'plugins/change-control/change-requests/{request_id}/')
    return page.url


def submit(page):
    page.get_by_role('button', name='Submit for review').click()
    expect(page.get_by_text('Needs review').first).to_be_visible()


def approve(page, url):
    page.goto(url)
    page.select_option('select[name=decision]', 'approve')
    page.get_by_role('button', name='Submit review').click()
    expect(page.get_by_text('Review recorded.')).to_be_visible()


def report_check(api, url, name, status):
    request_id = url.rstrip('/').rsplit('/', 1)[1]
    check = api.call('GET', f'plugins/change-control/checks/?change_request_id={request_id}&name={name}')['results'][0]
    api.call('PATCH', f'plugins/change-control/checks/{check["id"]}/', {'status': status})


def test_a_change_is_reviewed_merged_and_kept_on_record(api, people, branch, login):
    requester, reviewer = login(people.requester), login(people.reviewer)
    url = open_request(requester, api, branch)
    submit(requester)
    approve(reviewer, url)

    requester.goto(url)
    expect(requester.get_by_role('button', name='Merge branch')).to_be_disabled()
    report_check(api, url, people.check, 'success')
    requester.reload()
    requester.get_by_role('link', name='Merge branch').click()
    requester.check('input[type=checkbox][name=commit]')
    requester.click('button[name=_update]')

    for _ in range(60):
        requester.goto(url)
        if requester.get_by_text('Completed', exact=True).count():
            break
        time.sleep(1)
    expect(requester.get_by_text('Completed', exact=True).first).to_be_visible()
    group = api.call('GET', f'dcim/site-groups/?name={branch["name"]}')['results'][0]
    api.created.append(f'dcim/site-groups/{group["id"]}/')

    api.call('DELETE', f'plugins/branching/branches/{branch["id"]}/')
    requester.goto(f'{url}changes/')
    expect(requester.get_by_text('The changes as merged on')).to_be_visible()
    expect(requester.get_by_text(branch['name']).first).to_be_visible()


def test_resolving_from_a_stale_page_keeps_the_thread_resolved(api, people, branch, login):
    requester, reviewer = login(people.requester), login(people.reviewer)
    url = open_request(requester, api, branch)
    submit(requester)

    reviewer.goto(url)
    reviewer.get_by_role('link', name=re.compile('^Changes')).click()
    reviewer.fill('textarea[name=text]', 'Is this group name right?')
    reviewer.get_by_role('button', name='Comment').click()
    stale = reviewer.context.new_page()
    stale.goto(reviewer.url)

    reviewer.get_by_role('button', name='Resolve').click()
    expect(reviewer.get_by_role('button', name='Reopen')).to_be_visible()
    stale.get_by_role('button', name='Resolve').click()
    stale.wait_for_load_state()

    reviewer.goto(reviewer.url)
    expect(reviewer.get_by_text('Resolved', exact=True)).to_be_visible()
    expect(reviewer.get_by_role('button', name='Reopen')).to_be_visible()


def test_deleting_the_branch_abandons_the_request(api, people, branch, login):
    requester = login(people.requester)
    url = open_request(requester, api, branch)
    submit(requester)

    requester.goto(f'/plugins/branching/branches/{branch["id"]}/delete/')
    requester.get_by_role('button', name='Delete').click()

    requester.goto(url)
    expect(requester.get_by_text('Abandoned', exact=True).first).to_be_visible()
    expect(requester.get_by_role('button', name='Reopen')).to_have_count(0)


def test_a_closed_window_stops_the_automatic_merge(api, people, branch, login):
    requester, reviewer = login(people.requester), login(people.reviewer)
    closed = datetime.now(UTC) - timedelta(hours=1)
    url = open_request(
        requester,
        api,
        branch,
        scheduled_start=(closed - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'),
        scheduled_end=closed.strftime('%Y-%m-%d %H:%M'),
        auto_merge='on',
    )
    submit(requester)
    approve(reviewer, url)

    requester.goto(url)
    expect(requester.get_by_text('This change will not merge automatically.')).to_be_visible()
    expect(requester.get_by_text('The change window closed before the merge.')).to_be_visible()
