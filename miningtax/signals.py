import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender='authentication.CharacterOwnership')
def on_character_registered(sender, instance, created, **kwargs):
    """
    Wird aufgerufen wenn ein neuer Character in Alliance Auth registriert wird.
    Triggert einen asynchronen Celery Task der den Mining-Ledger des Characters synct.
    """
    if not created:
        return

    character = instance.character
    logger.info(f'Neuer Character registriert: {character.character_name} — starte Mining Sync')

    try:
        from .tasks import sync_character_mining_task
        sync_character_mining_task.delay(character.character_id)
    except Exception as e:
        logger.warning(f'Mining Sync Task für {character.character_name} konnte nicht gestartet werden: {e}')