from django.apps import AppConfig


class MiningtaxConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'miningtax'
    label = 'miningtax'

    def ready(self):
        # Registriert Menü-Hooks bei Alliance Auth
        from . import auth_hooks  # noqa
        # Registriert Signals (z.B. Auto-Sync bei neuem Character)
        from . import signals  # noqa
        # The daily sync is scheduled in local.py (CELERYBEAT_SCHEDULE), not
        # created here: an app must not create periodic tasks on its own, and
        # doing both made the sync run twice a night (see the README).