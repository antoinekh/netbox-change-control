"""
Global search.

NetBox auto-imports a plugin's `search` module, and a model with no index registered here
simply never appears in the search box, however well it is filtered on its own list page.

That was the state of every model in this plugin, which mattered most for `ChangeRequest.ref`:
the field exists so a change can be found by the ticket that spawned it, and the one place
somebody would type a ticket number is the search box.

Weights follow NetBox's own convention: lower sorts first, so the most identifying field on
each model carries the smallest number. 100 is a name or an identifier, 500 a description,
5000 free prose.
"""

from netbox.search import SearchIndex, register_search

from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)

__all__ = (
    'ChangeCommentIndex',
    'ChangeRequestIndex',
    'MergeCheckIndex',
    'PolicyIndex',
    'PolicyRuleIndex',
    'ReviewIndex',
)


@register_search
class ChangeRequestIndex(SearchIndex):
    """
    `ref` is weighted above the title deliberately. It is an exact external identifier, so
    somebody typing `CHG0012345` means that one request and nothing else.

    `branch_name` is indexed rather than the branch itself, because it is the copy that
    survives the branch being deleted, and a change request outliving its branch is exactly
    when search is the only way left to find it.
    """

    model = ChangeRequest
    fields = (
        ('ref', 100),
        ('title', 150),
        ('branch_name', 500),
        ('description', 500),
        ('comments', 5000),
    )
    display_attrs = ('ref', 'status', 'priority', 'requester', 'branch_name', 'description')


@register_search
class PolicyIndex(SearchIndex):
    model = Policy
    fields = (
        ('name', 100),
        ('description', 500),
        ('comments', 5000),
    )
    display_attrs = ('description',)


@register_search
class PolicyRuleIndex(SearchIndex):
    model = PolicyRule
    fields = (('name', 100),)
    display_attrs = ('policy', 'min_reviews')


@register_search
class ReviewIndex(SearchIndex):
    """
    A review is found by what the reviewer wrote. There is nothing else on it to search: the
    decision is a choice field and the reviewer is a relation, both of which the list page
    filters far better than free text would.
    """

    model = Review
    fields = (('comment', 1000),)
    display_attrs = ('change_request', 'reviewer', 'decision')


@register_search
class MergeCheckIndex(SearchIndex):
    model = MergeCheck
    fields = (
        ('name', 100),
        ('label', 150),
        ('summary', 1000),
    )
    display_attrs = ('change_request', 'status', 'summary')


@register_search
class ChangeCommentIndex(SearchIndex):
    """
    `change_label` is the name of the object the comment was about, kept on the comment so the
    discussion still makes sense once the branch and its diff are gone. Indexing it is what
    lets somebody find the conversation about a device months later.
    """

    model = ChangeComment
    fields = (
        ('change_label', 500),
        ('text', 1000),
    )
    display_attrs = ('change_request', 'author', 'change_label')
