from django.db import migrations, models


def seed_history_from_current_rates(apps, schema_editor):
    """
    Backfills one TaxRateHistory row per existing TaxRate, so every category
    that already had a rate before this migration has a documented starting
    point for get_tax_rate() to find.

    effective_from is set far in the past (2020-01-01, well before this
    plugin's own earliest possible ledger data) rather than "today": these
    rates have already been in force for however long the category has
    existed, and dating them "today" would make every entry mined before
    today look like it has no applicable history, falling through to the live
    TaxRate table — which happens to give the same numeric answer right now,
    but only by coincidence, and stops being true the moment someone changes
    a rate. Backdating properly means the fallback path is never silently
    relied upon for old data.
    """
    TaxRate = apps.get_model('miningtax', 'TaxRate')
    TaxRateHistory = apps.get_model('miningtax', 'TaxRateHistory')

    from datetime import date
    epoch = date(2020, 1, 1)

    for rate in TaxRate.objects.all():
        TaxRateHistory.objects.get_or_create(
            ore_category=rate.ore_category,
            effective_from=epoch,
            defaults={'tax_rate': rate.tax_rate},
        )


def noop_reverse(apps, schema_editor):
    # Reversing this migration removes the model (see operations below), which
    # takes the seeded rows with it — nothing extra to undo here.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0021_alliancebillingrecord_member_snapshot'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaxRateHistory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ore_category', models.CharField(db_index=True, max_length=50)),
                ('tax_rate', models.DecimalField(decimal_places=2, max_digits=5)),
                ('effective_from', models.DateField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'tax rate history',
                'verbose_name_plural': 'tax rate history',
                'ordering': ('ore_category', '-effective_from'),
                'default_permissions': (),
            },
        ),
        migrations.AlterUniqueTogether(
            name='taxratehistory',
            unique_together={('ore_category', 'effective_from')},
        ),
        migrations.RunPython(seed_history_from_current_rates, noop_reverse),
    ]
