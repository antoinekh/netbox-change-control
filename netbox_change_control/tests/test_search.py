"""
Global search.

NetBox auto-imports a plugin's `search` module, and a model with no index registered there
never appears in the search box however well its own list page filters. Every model in this
plugin was in that state.

It mattered most for `ChangeRequest.ref`: the field exists so a change can be found by the
ticket that spawned it, and the search box is the one place somebody types a ticket number.
"""

from django.test import TestCase
from netbox.registry import registry
from netbox.search.backends import search_backend
from users.models import Group, User

from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import (
    ChangeComment,
    ChangeRequest,
    MergeCheck,
    Policy,
    PolicyRule,
    Review,
)
from netbox_change_control.tests.base import make_branch

INDEXED_MODELS = (ChangeComment, ChangeRequest, MergeCheck, Policy, PolicyRule, Review)


class EveryModelIsIndexedTest(TestCase):
    def test_each_model_has_a_search_index(self):
        """
        A model added later must be indexed too, or it silently drops out of search.
        """
        missing = [
            model._meta.model_name
            for model in INDEXED_MODELS
            if f'netbox_change_control.{model._meta.model_name}' not in registry['search']
        ]
        self.assertEqual(missing, [], 'these models have no search index registered')

    def test_the_indexed_fields_all_exist(self):
        """
        A field name that does not resolve is cached as an attribute instead, silently, so a
        typo produces an index that finds nothing.
        """
        for model in INDEXED_MODELS:
            indexer = registry['search'][f'netbox_change_control.{model._meta.model_name}']
            for name, _weight in indexer.fields:
                with self.subTest(model=model._meta.model_name, field=name):
                    model._meta.get_field(name)
            for name in indexer.display_attrs:
                with self.subTest(model=model._meta.model_name, attr=name):
                    self.assertTrue(
                        hasattr(model, name) or model._meta.get_field(name),
                        f'{model.__name__}.{name} does not exist',
                    )


class SearchFindsThingsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create(username='requester')
        cls.reviewer = User.objects.create(username='reviewer')
        cls.group = Group.objects.create(name='Engineers')

    def setUp(self):
        self.branch = make_branch('searchable', self._testMethodName)
        self.cr = ChangeRequest.objects.create(
            branch=self.branch,
            ref='CHG0012345',
            title='Upgrade the access switch',
            description='replace a failing line card',
            requester=self.requester,
        )

    def index_everything(self):
        """
        Index every object this test has written.

        `search_backend.cache()` is the same call NetBox's own signal handlers end at, and it
        resolves the indexer out of the registry, so a model with none registered caches
        nothing and the search below finds nothing. That is the behaviour these tests are
        about, and it is reached here through public API only.

        Calling it directly is what avoids depending on the deferred pipeline NetBox 4.7 put
        in front of it, which a test cannot drive without reaching inside it: a `TestCase`
        never commits, so the `on_commit` callback never runs; the callback indexes inline
        only when no RQ worker is listening, and a development stack runs one; and the
        callback is registered once per transaction, so `setUp` provisioning a branch claims
        it and `captureOnCommitCallbacks` around a later write captures nothing. That
        pipeline is NetBox's to test, and its own module says it is not plugin API.

        One call per model: `cache()` reads the indexer from the first instance it is given
        and applies it to the rest, so a mixed iterable would index everything as whatever
        came first.
        """
        for model in INDEXED_MODELS:
            search_backend.cache(model.objects.all())

    def found(self, term):
        self.index_everything()
        return {(r.object._meta.model_name, r.object.pk) for r in search_backend.search(term)}

    def test_a_change_request_is_found_by_its_reference(self):
        """
        The reason this file exists. A pipeline or a colleague has a ticket number and nothing
        else.
        """
        self.assertIn(('changerequest', self.cr.pk), self.found('CHG0012345'))

    def test_a_change_request_is_found_by_its_title(self):
        self.assertIn(('changerequest', self.cr.pk), self.found('access switch'))

    def test_a_change_request_is_found_by_its_description(self):
        self.assertIn(('changerequest', self.cr.pk), self.found('line card'))

    def test_a_change_request_is_found_by_its_branch_name_after_the_branch_is_gone(self):
        """
        The stored branch name is indexed rather than the branch itself, because a request
        outliving its branch is exactly when search is the only way left to find it.
        """
        name = self.branch.name
        self.branch.delete()
        self.cr.refresh_from_db()
        self.cr.save()

        self.assertTrue(self.cr.branch_deleted)
        self.assertIn(('changerequest', self.cr.pk), self.found(name))

    def test_a_policy_is_found_by_name(self):
        policy = Policy.objects.create(name='Circuit changes', description='customer facing')
        self.assertIn(('policy', policy.pk), self.found('Circuit changes'))

    def test_a_policy_rule_is_found_by_name(self):
        policy = Policy.objects.create(name='Device changes')
        rule = PolicyRule.objects.create(policy=policy, name='Two engineers', min_reviews=2)
        self.assertIn(('policyrule', rule.pk), self.found('Two engineers'))

    def test_a_review_is_found_by_its_comment(self):
        review = Review.objects.create(
            change_request=self.cr,
            reviewer=self.reviewer,
            decision=ReviewDecisionChoices.REJECT,
            comment='the loopback address is wrong',
        )
        self.assertIn(('review', review.pk), self.found('loopback address'))

    def test_a_check_is_found_by_its_summary(self):
        check = MergeCheck.objects.create(
            change_request=self.cr,
            name='ci-pipeline',
            label='CI pipeline',
            status=MergeCheckStatusChoices.FAILURE,
            summary='config render failed on core-sw-1',
        )
        self.assertIn(('mergecheck', check.pk), self.found('config render failed'))

    def test_a_comment_is_found_by_its_text_and_by_what_it_was_about(self):
        comment = ChangeComment.objects.create(
            change_request=self.cr,
            author=self.reviewer,
            text='are we sure about this rack?',
            change_label='dmi01-akron-rtr01',
        )
        self.assertIn(('changecomment', comment.pk), self.found('sure about this rack'))
        self.assertIn(('changecomment', comment.pk), self.found('dmi01-akron-rtr01'))

    def test_an_unrelated_term_finds_nothing_of_ours(self):
        ours = {name for name, _pk in self.found('zzz-nothing-matches-this')}
        self.assertEqual(ours & {m._meta.model_name for m in INDEXED_MODELS}, set())
