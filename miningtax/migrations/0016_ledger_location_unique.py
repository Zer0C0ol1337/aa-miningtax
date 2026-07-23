# Makes location part of a ledger entry's identity.
#
# One row per character/date/ore meant belt mining was lost whenever the same
# ore was also mined at a moon that day: the personal sync skipped it rather
# than overwrite the more precise structure entry, and there was nowhere else
# to record it. Including the location lets both coexist.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0015_orecategoryrule'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='miningledgerentry',
            unique_together={('character', 'date', 'type_id', 'solar_system_id')},
        ),
    ]