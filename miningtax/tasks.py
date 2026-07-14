import logging
from datetime import date

from celery import shared_task

from .services import sync_all_characters, sync_all_corp_observers, update_market_prices

logger = logging.getLogger(__name__)


# Täglicher Sync: persönliche Ledger + Corp Observer + Marktpreise + Billing Records + Zahlungsprüfung
@shared_task
def daily_mining_sync():
    from .billing import save_billing_records_for_month
    from .payments import check_corp_payments

    synced_chars = sync_all_characters()
    synced_corps = sync_all_corp_observers()
    priced = update_market_prices()

    today = date.today()
    billing_saved = save_billing_records_for_month(today.year, today.month)

    # Prüft ob Corps ihre Steuer bereits überwiesen haben (Wallet Journal)
    payments_matched = check_corp_payments(today.year, today.month)

    logger.info(
        f'Daily sync: {synced_chars} persönliche, {synced_corps} Corp Observer, '
        f'{priced} Preise, {billing_saved} Billing Records, {payments_matched} Zahlungen erkannt'
    )

    return (
        f'{synced_chars} persönliche Einträge, '
        f'{synced_corps} Corp Observer Einträge, '
        f'{priced} Preise aktualisiert, '
        f'{billing_saved} Billing Records gespeichert, '
        f'{payments_matched} Zahlungen automatisch erkannt'
    )


# Wird getriggert wenn ein neuer Character in Alliance Auth registriert wird
@shared_task
def sync_character_mining_task(character_id):
    """Synct den Mining-Ledger eines einzelnen Characters asynchron."""
    try:
        from allianceauth.eveonline.models import EveCharacter
        from .services import sync_character_mining

        character = EveCharacter.objects.get(character_id=character_id)
        synced = sync_character_mining(character)
        logger.info(f'Auto-Sync für {character.character_name}: {synced} Einträge')
        return synced

    except Exception as e:
        logger.warning(f'Auto-Sync für Character {character_id} fehlgeschlagen: {e}')
        return 0