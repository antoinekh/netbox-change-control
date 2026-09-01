"""
The permission constraints the administration guide tells people to use.

NetBox object permissions carry an optional `constraints` queryset filter, and the `$user`
token in one resolves to the signed-in user. That is a NetBox feature, not something this
plugin adds: the token is `users.constants.CONSTRAINT_TOKEN_USER`, it is substituted by
`ObjectPermissionBackend`, and NetBox core uses it itself for bookmarks and notifications.

What this plugin does add is the claim that a particular field name works for a particular
model. `{"reviewer": "$user"}` is only useful advice if `Review.reviewer` is really the field
the constraint matches on, so these check each one the guide prints.
"""

import re

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from netbox_branching.models import ChangeDiff
from users.models import ObjectPermission, User

from netbox_change_control.choices import MergeCheckStatusChoices, ReviewDecisionChoices
from netbox_change_control.models import ChangeComment, ChangeRequest, MergeCheck, Policy, Review
from netbox_change_control.tests.base import docs_page, make_branch


def constrain(user, model, actions, constraints):
    permission = ObjectPermission.objects.create(
        name=f'{model._meta.model_name}-{user.pk}', actions=list(actions), constraints=constraints
    )
    permission.object_types.add(ContentType.objects.get_for_model(model))
    permission.users.add(user)
    return permission


class DocumentedConstraintsWorkTest(TestCase):
    """
    Each row of the guide's constraint table, checked against the real permission backend.
    """

    @classmethod
    def setUpTestData(cls):
        cls.mine = User.objects.create(username='mine')
        cls.theirs = User.objects.create(username='theirs')
        cls.requester = User.objects.create(username='requester')

    def setUp(self):
        self.branch = make_branch('constraint', self._testMethodName)
        self.cr_mine = ChangeRequest.objects.create(branch=self.branch, title='Mine', requester=self.mine)
        self.cr_theirs = ChangeRequest.objects.create(
            branch=make_branch('constraint2', self._testMethodName), title='Theirs', requester=self.theirs
        )

    def test_a_reviewer_constraint_limits_deletion_to_own_reviews(self):
        """
        The guide's headline example, and the reason it is there: `delete_review` is otherwise
        model-wide, and deleting somebody's rejection changes the outcome of the gate.
        """
        ours = Review.objects.create(
            change_request=self.cr_theirs, reviewer=self.mine, decision=ReviewDecisionChoices.APPROVE
        )
        not_ours = Review.objects.create(
            change_request=self.cr_mine, reviewer=self.theirs, decision=ReviewDecisionChoices.APPROVE
        )
        constrain(self.mine, Review, ['view', 'delete'], {'reviewer': '$user'})
        user = User.objects.get(pk=self.mine.pk)

        self.assertTrue(user.has_perm('netbox_change_control.delete_review', ours))
        self.assertFalse(user.has_perm('netbox_change_control.delete_review', not_ours))

    def test_an_author_constraint_limits_a_comment_to_its_writer(self):
        diff = ChangeDiff.objects.create(
            branch=self.branch,
            object_type=ContentType.objects.get_for_model(Policy),
            object_id=1,
            object_repr='x',
            action='update',
        )
        ours = ChangeComment.objects.create(
            change_request=self.cr_mine, change_diff=diff, author=self.mine, text='mine'
        )
        not_ours = ChangeComment.objects.create(
            change_request=self.cr_mine, change_diff=diff, author=self.theirs, text='theirs'
        )
        constrain(self.mine, ChangeComment, ['view', 'delete'], {'author': '$user'})
        user = User.objects.get(pk=self.mine.pk)

        self.assertTrue(user.has_perm('netbox_change_control.delete_changecomment', ours))
        self.assertFalse(user.has_perm('netbox_change_control.delete_changecomment', not_ours))

    def test_a_requester_constraint_limits_a_change_request_to_its_owner(self):
        constrain(self.mine, ChangeRequest, ['view', 'change'], {'requester': '$user'})
        user = User.objects.get(pk=self.mine.pk)

        self.assertTrue(user.has_perm('netbox_change_control.change_changerequest', self.cr_mine))
        self.assertFalse(user.has_perm('netbox_change_control.change_changerequest', self.cr_theirs))

    def test_a_name_constraint_limits_a_ci_token_to_its_own_check(self):
        """
        The shape the guide recommends for a reporting token: it can report the check it owns
        and touch nothing else.
        """
        ours = MergeCheck.objects.create(
            change_request=self.cr_mine, name='ci-pipeline', status=MergeCheckStatusChoices.PENDING
        )
        not_ours = MergeCheck.objects.create(
            change_request=self.cr_mine, name='cab-approval', status=MergeCheckStatusChoices.PENDING
        )
        constrain(self.mine, MergeCheck, ['view', 'change'], {'name': 'ci-pipeline'})
        user = User.objects.get(pk=self.mine.pk)

        self.assertTrue(user.has_perm('netbox_change_control.change_mergecheck', ours))
        self.assertFalse(user.has_perm('netbox_change_control.change_mergecheck', not_ours))


class DocumentedConstraintFieldsExistTest(TestCase):
    """
    Every field the guide's constraint table names has to be a real field, or the advice is a
    filter that silently matches nothing.
    """

    def test_each_constraint_field_resolves(self):
        table = re.search(
            r'\| Goal \| Object type \| Constraint \|(.*?)\n\n',
            docs_page('admin-guide.md'),
            re.S,
        )
        self.assertIsNotNone(table, 'the constraint table is no longer in the administration guide')

        models = {
            'Review': Review,
            'Change Comment': ChangeComment,
            'Change Request': ChangeRequest,
            'Merge Check': MergeCheck,
        }
        rows = re.findall(r'\|([^|]+)\|([^|]+)\|\s*`([^`]+)`\s*\|', table.group(1))
        self.assertTrue(rows, 'no constraint rows were parsed')

        for _goal, object_type, constraint in rows:
            model = models.get(object_type.strip())
            self.assertIsNotNone(model, f'the guide names an object type this test does not know: {object_type}')
            for field in re.findall(r'"(\w+)":', constraint):
                with self.subTest(model=model.__name__, field=field):
                    model._meta.get_field(field)
