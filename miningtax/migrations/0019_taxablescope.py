# Limits taxation to characters in a configured alliance or corporation.
#
# Player accounts routinely hold characters with no connection to the alliance —
# high-sec alts, trade characters, corps left behind — whose mining still
# reaches the plugin through the personal ledger and was billed like any other.
#
# The table is left empty on purpose: an empty scope taxes everything, which is
# what every install did before, so upgrading changes nothing until an officer
# decides what counts.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eveonline', '0025_remove_evecharacter_last_updated_and_more'),
        ('miningtax', '0018_general_permission_labels'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaxableScope',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.CharField(blank=True, max_length=255)),
                ('alliance', models.ForeignKey(blank=True, help_text='Tax every character in this alliance', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='miningtax_scopes', to='eveonline.eveallianceinfo')),
                ('corporation', models.ForeignKey(blank=True, help_text='Tax every character in this corporation, whatever alliance it is in', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='miningtax_scopes', to='eveonline.evecorporationinfo')),
            ],
            options={
                'verbose_name': 'taxable scope',
                'default_permissions': (),
            },
        ),
    ]