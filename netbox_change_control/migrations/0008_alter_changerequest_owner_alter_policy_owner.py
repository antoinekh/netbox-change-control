"""
NetBox 4.7 adds `related_name='+'` to `OwnerMixin.owner`, dropping the automatic reverse
accessor an Owner used to get for every model which inherits it. Nothing in this plugin reads
that accessor, so this changes no behaviour and writes no SQL; Django only needs the field
state to match, or `makemigrations --check` fails on every run.

This is why the 0.4.x line and this one cannot share a migration history: the same file makes
`--check` fail on NetBox 4.6, where the field carries no `related_name`.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        # `users` is not named again: 0001_initial already depends on its latest migration,
        # so the Owner model this field points at is in state by the time we get here.
        ('netbox_change_control', '0007_alter_changerequestpolicy_options'),
    ]

    operations = [
        migrations.AlterField(
            model_name='changerequest',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='users.owner',
            ),
        ),
        migrations.AlterField(
            model_name='policy',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='users.owner',
            ),
        ),
    ]
