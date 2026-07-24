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


# ─── LOOKUP CACHE INVALIDATION ────────────────────────────────────────────────
#
# Billing holds the small lookup tables for a minute rather than re-reading them
# for every ledger entry. Signals rather than a call in each view: the tables are
# also edited through the Django admin, and a cache that only notices changes
# made through one of two paths is worse than no cache — the figures would be
# right or stale depending on where the officer happened to click.

from django.db.models.signals import post_save, post_delete
from .models import (
    OreCategory, TaxRate, TaxExemption, FleetSession, AllianceMoon,
    MoonRental, TaxableScope, OreCategoryRule,
)


def _invalidate_billing_caches(sender, **kwargs):
    from .billing import invalidate_billing_caches
    invalidate_billing_caches()


# EveCorporationInfo belongs to Alliance Auth, not to this plugin, but a corp
# changing alliance decides who is in scope — so its updates have to clear the
# cache too, or a corp that just joined or left would keep its old billing
# treatment until the entry expired.
from allianceauth.eveonline.models import EveCorporationInfo

for _model in (
    OreCategory, TaxRate, TaxExemption, FleetSession, AllianceMoon,
    MoonRental, TaxableScope, OreCategoryRule, EveCorporationInfo,
):
    post_save.connect(_invalidate_billing_caches, sender=_model, weak=False)
    post_delete.connect(_invalidate_billing_caches, sender=_model, weak=False)