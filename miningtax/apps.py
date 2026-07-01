from django.apps import AppConfig


class MiningtaxConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'miningtax'
    label = 'miningtax'

    def ready(self):
        # registriert unsere Hooks (Menü-Eintrag) bei Alliance Auth, sobald die App geladen ist
        from . import auth_hooks  # noqa