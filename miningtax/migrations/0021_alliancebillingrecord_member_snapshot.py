from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0020_rename_daily_sync_task'),
    ]

    operations = [
        migrations.AddField(
            model_name='alliancebillingrecord',
            name='member_snapshot',
            field=models.JSONField(null=True, blank=True),
        ),
    ]