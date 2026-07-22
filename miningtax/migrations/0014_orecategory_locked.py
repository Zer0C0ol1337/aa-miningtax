# Protects hand-picked ore categories from the automatic import

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0013_taxexemption'),
    ]

    operations = [
        migrations.AddField(
            model_name='orecategory',
            name='locked',
            field=models.BooleanField(
                default=False,
                help_text="Keep this category as set; the ore import won't touch it",
            ),
        ),
    ]