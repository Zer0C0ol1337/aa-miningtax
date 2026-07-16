from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0010_sovsystem_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='alliancemoon',
            name='structure_name',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]