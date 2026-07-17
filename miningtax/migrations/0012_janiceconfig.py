from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0011_alliancemoon_structure_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='JaniceConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False, help_text='Use Janice refined value for ore pricing. When off, ESI adjusted_price is used.')),
                ('api_key', models.CharField(blank=True, max_length=255, help_text='Janice API key (request one via their Discord). Stored server-side, never shown to members.')),
            ],
            options={
                'verbose_name': 'Janice configuration',
                'default_permissions': (),
            },
        ),
    ]