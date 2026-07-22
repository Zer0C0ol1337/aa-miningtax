# Generated for the tax exemption feature

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eveonline', '0025_remove_evecharacter_last_updated_and_more'),
        ('miningtax', '0012_janiceconfig'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaxExemption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(blank=True, help_text='Optional note why this character/corp is exempt', max_length=255)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('character', models.ForeignKey(blank=True, help_text='Exempt a single character (leave blank when exempting a whole corporation)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='miningtax_exemptions', to='eveonline.evecharacter')),
                ('corporation', models.ForeignKey(blank=True, help_text='Exempt an entire corporation (leave blank when exempting a single character)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='miningtax_exemptions', to='eveonline.evecorporationinfo')),
            ],
            options={
                'default_permissions': (),
            },
        ),
    ]