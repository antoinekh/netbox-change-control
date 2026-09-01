from django.db import migrations

CODENAMES = (
    'add_changerequestpolicy',
    'change_changerequestpolicy',
    'delete_changerequestpolicy',
    'view_changerequestpolicy',
)


def drop_stale_permissions(apps, schema_editor):
    """
    Django creates the four permissions on the first migration and never removes them again,
    so an existing install keeps rows the plugin no longer defines. Removing them here stops
    the permission form offering an action nothing reads.
    """
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')

    content_type = ContentType.objects.filter(app_label='netbox_change_control', model='changerequestpolicy').first()
    if content_type:
        Permission.objects.filter(content_type=content_type, codename__in=CODENAMES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0001_initial'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('netbox_change_control', '0006_remove_changerequestpolicy_created_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='changerequestpolicy',
            options={'default_permissions': (), 'ordering': ('policy__weight', 'policy__name')},
        ),
        # Reversing this needs the model definition back as well, and Django recreates the
        # permissions itself once it is, so there is nothing to undo here.
        migrations.RunPython(drop_stale_permissions, migrations.RunPython.noop),
    ]
