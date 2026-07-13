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