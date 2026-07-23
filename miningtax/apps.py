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

        # Legt den täglichen Sync als Periodic Task an, falls er fehlt.
        # Über post_migrate statt direkt hier: in ready() ist die Datenbank
        # noch nicht verlässlich erreichbar, und ein Fehler an dieser Stelle
        # würde den Start der gesamten Seite verhindern.
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_default_schedule, sender=self)


def _create_default_schedule(sender, **kwargs):
    from .schedule import ensure_daily_sync_schedule
    ensure_daily_sync_schedule()