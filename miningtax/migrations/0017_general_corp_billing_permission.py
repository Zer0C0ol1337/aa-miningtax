# Adds a corp_billing permission, replacing the automatic CEO access.
#
# Access used to be granted by detecting CEOs from EveCorporationInfo.ceo_id.
# That put the plugin in charge of deciding who counts as an officer: the grant
# was invisible in the permission UI, could not be revoked by whoever runs the
# Auth instance, and came along with any alt who happened to be CEO of an
# unrelated corp. The capability is now a permission like any other, and who
# holds it is a decision for the Auth admin.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0016_ledger_location_unique'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='general',
            options={
                'default_permissions': (),
                'managed': False,
                'permissions': (
                    ('basic_access', 'Can view Mining Tax'),
                    ('corp_billing', "Can view own corporation's billing (Corp Officer)"),
                    ('mining_officer', 'Can access Alliance Billing and Settings (Mining Officer)'),
                ),
                'verbose_name': 'general',
                'verbose_name_plural': 'general',
            },
        ),
    ]