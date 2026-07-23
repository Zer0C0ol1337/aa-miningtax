# Points existing schedules at the renamed daily sync task.
#
# The task was called daily_mining_sync while every other one ended in _task.
# Renaming the function is not enough on its own: django-celery-beat stores the
# task as a string, so a schedule created before this would keep pointing at a
# path that no longer exists. Celery does not treat that as an error it can
# report anywhere useful — the task is simply never found, and the daily sync
# stops without anything saying so, which is the failure mode this whole release
# has been about.

from django.db import migrations

OLD_TASK = 'miningtax.tasks.daily_mining_sync'
NEW_TASK = 'miningtax.tasks.daily_mining_sync_task'


def _repoint(apps, schema_editor, frm, to):
    try:
        PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    except LookupError:
        # django-celery-beat absent, so there are no schedules to repoint.
        return
    PeriodicTask.objects.filter(task=frm).update(task=to)


def forwards(apps, schema_editor):
    _repoint(apps, schema_editor, OLD_TASK, NEW_TASK)


def backwards(apps, schema_editor):
    _repoint(apps, schema_editor, NEW_TASK, OLD_TASK)


class Migration(migrations.Migration):

    dependencies = [
        ('miningtax', '0019_taxablescope'),
    ]

    # Not declared as a dependency on django_celery_beat: the app is optional,
    # and requiring it would break installs that run this plugin without Celery
    # beat. The lookup above handles its absence instead.
    operations = [
        migrations.RunPython(forwards, backwards),
    ]