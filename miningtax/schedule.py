"""
Registers the daily sync as a periodic task on startup.

Scheduling used to be left to the administrator, who had to add an entry to
local.py by hand. Forgetting it produced no error and no warning — the plugin
looked like it was working while nothing ever synced, and the omission only
surfaced when someone noticed their ledger was weeks out of date. The schedule
is created here instead, so a fresh install syncs from the start.

It is created once and then left alone: the entry is an ordinary row in
django-celery-beat's table, so changing the time, disabling it, or deleting it
in the admin does exactly what it looks like it does, and this code will not
undo that on the next restart.
"""
import logging

logger = logging.getLogger(__name__)

TASK_NAME = 'miningtax.tasks.daily_mining_sync_task'

# What the task was called before 0.11.0. Kept so an existing schedule is
# recognised as the same thing rather than being duplicated alongside the new
# one, and so migration 0020 has a single place to read the old name from.
LEGACY_TASK_NAME = 'miningtax.tasks.daily_mining_sync'

SCHEDULE_NAME = 'miningtax: daily mining sync'

# 02:00 rather than midnight: ESI is busiest right after downtime, and nothing
# about this needs to be the first thing to run.
DEFAULT_HOUR = 2
DEFAULT_MINUTE = 0


def ensure_daily_sync_schedule():
    """
    Creates the periodic task if it is missing. Returns True when it created
    one, False otherwise.

    Deliberately quiet about failure. This runs during app startup, where an
    unhandled exception takes down the whole site — a missing schedule is worth
    a log line, not an outage. The most likely cause is being called before the
    tables exist (during migrate on a fresh install), and the next start fixes
    that by itself.
    """
    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except ImportError:
        logger.debug('django_celery_beat not installed, skipping schedule setup')
        return False

    try:
        # Matching on the task rather than the entry name so a schedule an
        # admin renamed is still recognised, instead of being duplicated. The
        # pre-0.11.0 name counts too: migration 0020 rewrites those, but a
        # schedule created between the two would otherwise appear twice.
        if PeriodicTask.objects.filter(
            task__in=[TASK_NAME, LEGACY_TASK_NAME]
        ).exists():
            return False

        crontab, _ = CrontabSchedule.objects.get_or_create(
            minute=str(DEFAULT_MINUTE),
            hour=str(DEFAULT_HOUR),
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
        )
        PeriodicTask.objects.create(
            name=SCHEDULE_NAME,
            task=TASK_NAME,
            crontab=crontab,
            description=(
                'Syncs corp observers and personal mining ledgers, refreshes '
                'prices and the ore list, then writes billing records. '
                'Edit or disable here; the plugin only creates it once.'
            ),
        )
        logger.info(
            f'Created periodic task "{SCHEDULE_NAME}" at '
            f'{DEFAULT_HOUR:02d}:{DEFAULT_MINUTE:02d}'
        )
        return True

    except Exception as e:
        logger.debug(f'Could not set up the daily sync schedule yet: {e}')
        return False