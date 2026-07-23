# Renames the permission labels to the three tiers: View, Corp, Admin.
#
# Labels only — codenames stay as they are, because group assignments point at
# them and renaming would quietly void every assignment already made.
#
# AlterModelOptions alone is not enough here. Django creates permissions from a
# model's Meta in a post_migrate handler that only inserts the ones missing,
# matching on codename; it never updates the name of a row that already exists.
# So a label changed in code stays changed only for fresh installs, while every
# existing instance keeps whatever text was written when the permission was
# first created. This plugin has carried that gap since 0009 — the admin list
# still reads "Can manage Mining Tax", the wording from 0003. The RunPython
# below closes it by updating the rows directly.

from django.db import migrations

TIER_LABELS = {
    'basic_access':   'View — own mining ledger and profile',
    'corp_billing':   'Corp — billing for own corporation only',
    'mining_officer': 'Admin — alliance-wide billing, settings and sync',
}

# What the labels were before, so the migration can be reversed cleanly.
PREVIOUS_LABELS = {
    'basic_access':   'Can view Mining Tax',
    'corp_billing':   "Can view own corporation's billing (Corp Officer)",
    'mining_officer': 'Can access Alliance Billing and Settings (Mining Officer)',
}


def _relabel(apps, schema_editor, labels):
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    ctype = ContentType.objects.filter(app_label='miningtax', model='general').first()
    if not ctype:
        # Nothing to rename yet: on a fresh install the permissions are created
        # after this migration, and they get the new labels from the model.
        return

    for codename, name in labels.items():
        Permission.objects.filter(
            content_type=ctype, codename=codename
        ).update(name=name)


def apply_labels(apps, schema_editor):
    _relabel(apps, schema_editor, TIER_LABELS)


def revert_labels(apps, schema_editor):
    _relabel(apps, schema_editor, PREVIOUS_LABELS)


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0017_general_corp_billing_permission'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='general',
            options={
                'default_permissions': (),
                'managed': False,
                'permissions': (
                    ('basic_access', 'View — own mining ledger and profile'),
                    ('corp_billing', 'Corp — billing for own corporation only'),
                    ('mining_officer', 'Admin — alliance-wide billing, settings and sync'),
                ),
                'verbose_name': 'general',
                'verbose_name_plural': 'general',
            },
        ),
        migrations.RunPython(apply_labels, revert_labels),
    ]