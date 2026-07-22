# Name-based rules for assigning ore to a category

from django.db import migrations, models


def seed_default_rules(apps, schema_editor):
    """
    Seeds the two rules this was built for. Both ores sit in ordinary asteroid
    groups, so without a rule they'd be taxed as plain ore. Categories are
    created without a tax rate on purpose — until an officer sets one, the
    Default rate applies, which is the safe direction to err in.
    """
    OreCategoryRule = apps.get_model('miningtax', 'OreCategoryRule')
    for contains, category, note in [
        ('prismaticite', 'Prismaticite', 'Phased belts only; reprocess yields one of eight minerals at random'),
        ('bezdnacine', 'Abyssal', 'Abyssal deadspace ore'),
        ('rakovene', 'Abyssal', 'Abyssal deadspace ore'),
        ('talassonite', 'Abyssal', 'Abyssal deadspace ore'),
    ]:
        OreCategoryRule.objects.get_or_create(
            contains=contains,
            match_field='type_name',
            defaults={'category': category, 'note': note, 'priority': 50},
        )


def unseed(apps, schema_editor):
    OreCategoryRule = apps.get_model('miningtax', 'OreCategoryRule')
    OreCategoryRule.objects.filter(
        contains__in=['prismaticite', 'bezdnacine', 'rakovene', 'talassonite']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0014_orecategory_locked'),
    ]

    operations = [
        migrations.CreateModel(
            name='OreCategoryRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('match_field', models.CharField(choices=[('type_name', 'Ore name'), ('group_name', 'EVE group name')], default='type_name', help_text='Whether to match against the ore name or its EVE group', max_length=20)),
                ('contains', models.CharField(help_text='Case-insensitive substring, e.g. "prismaticite" or "bezdnacine"', max_length=255)),
                ('category', models.CharField(help_text='Category to assign. Add a matching tax rate for it, otherwise the Default rate applies', max_length=50)),
                ('priority', models.PositiveSmallIntegerField(default=100, help_text='Lower runs first. Use it to let a specific rule beat a broader one')),
                ('active', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'verbose_name': 'ore category rule',
                'ordering': ('priority', 'contains'),
                'default_permissions': (),
            },
        ),
        migrations.RunPython(seed_default_rules, unseed),
    ]