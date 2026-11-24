"""
Fill what the merge record and the comments need, for the change requests made before them.

A comment learns which object it is about from its change, while that change exists. A
completed request whose branch still exists gets the record of its last merge from that branch,
which still holds the merged changes. Once a branch is deleted neither can be recovered.

netbox-branching runs the plugin migrations again in each branch it migrates, so both steps
skip what is already filled.
"""

from django.db import migrations


def fill_comment_objects(apps, schema_editor):
    ChangeComment = apps.get_model('netbox_change_control', 'ChangeComment')
    for comment in ChangeComment.objects.filter(object_id__isnull=True, change_diff__isnull=False).select_related(
        'change_diff'
    ):
        comment.object_type_id = comment.change_diff.object_type_id
        comment.object_id = comment.change_diff.object_id
        comment.save(update_fields=['object_type', 'object_id'])


def fill_merged_changes(apps, schema_editor):
    ChangeRequest = apps.get_model('netbox_change_control', 'ChangeRequest')
    HistoryEntry = apps.get_model('netbox_change_control', 'HistoryEntry')
    MergedChange = apps.get_model('netbox_change_control', 'MergedChange')
    ChangeDiff = apps.get_model('netbox_branching', 'ChangeDiff')

    for change_request in ChangeRequest.objects.filter(status='completed', branch__isnull=False):
        merge = (
            HistoryEntry.objects.filter(change_request=change_request, action='merged').order_by('-time', '-pk').first()
        )
        if merge is None or MergedChange.objects.filter(merge=merge).exists():
            continue
        MergedChange.objects.bulk_create(
            MergedChange(
                merge=merge,
                object_type_id=diff.object_type_id,
                object_id=diff.object_id,
                object_repr=diff.object_repr,
                action=diff.action,
                original=diff.original,
                modified=diff.modified,
            )
            for diff in ChangeDiff.objects.filter(branch_id=change_request.branch_id).order_by('-last_updated')
        )


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_change_control', '0015_mergedchange_and_comment_object'),
    ]

    operations = [
        migrations.RunPython(fill_comment_objects, migrations.RunPython.noop),
        migrations.RunPython(fill_merged_changes, migrations.RunPython.noop),
    ]
