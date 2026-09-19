from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Depends on the TaxRateHistory migration, not directly on
        # 0021 — this is the second new model added in the same release,
        # and chaining after it keeps the migration graph a single line
        # rather than branching, which is simpler to reason about later.
        ('miningtax', '0022_taxratehistory'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentCodeSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reveal_day', models.PositiveSmallIntegerField(
                    default=2,
                    help_text="Day of the month (1-28) the payment code becomes visible for the PREVIOUS month's billing. Kept to 28 or below so it exists in every month, including February.",
                )),
                ('reveal_hour_utc', models.PositiveSmallIntegerField(
                    default=11,
                    help_text='Hour (0-23, UTC) on that day the code is revealed. EVE downtime is around 11:00 UTC, which is why that was the original default — set to match whenever your data is expected to be complete.',
                )),
                ('hint_text', models.TextField(
                    default='The payment code will be available from the {reveal_day} of next month.',
                    help_text='Shown instead of the code before the reveal time. Use {reveal_day} and {reveal_time} as placeholders — both are filled in with the values above, so the text stays correct if you change the day or time later without having to edit this field again.',
                )),
            ],
            options={
                'verbose_name': 'payment code settings',
                'verbose_name_plural': 'payment code settings',
                'default_permissions': (),
            },
        ),
    ]
