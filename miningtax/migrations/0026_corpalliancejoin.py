from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Stores each corporation's alliance join date, fetched once from ESI, instead
    of asking ESI for every corp every night.
    """

    dependencies = [
        ('miningtax', '0025_paymentcodesettings_open_invoices_from'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorpAllianceJoin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('corporation_id', models.BigIntegerField(unique=True)),
                ('alliance_id', models.BigIntegerField(null=True, blank=True)),
                ('joined', models.DateField(null=True, blank=True)),
                ('fetched_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]