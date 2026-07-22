import logging
from datetime import date

from celery import shared_task

from .services import sync_all_characters, sync_all_corp_observers, update_market_prices

logger = logging.getLogger(__name__)


# Daily sync: personal ledgers + corp observer + market prices + sovereignty
# + billing records + payment check
@shared_task
def daily_mining_sync():
    from .billing import save_billing_records_for_month
    from .payments import check_corp_payments
    from .services import sync_sov_systems, sync_ore_categories

    # Refreshed first so any ore added to EVE is classified before the
    # ledgers that reference it are priced and taxed.
    ore_new, ore_updated = sync_ore_categories()

    synced_chars = sync_all_characters()
    synced_corps = sync_all_corp_observers()
    priced = update_market_prices()
    sov_systems = sync_sov_systems()

    today = date.today()
    billing_saved = save_billing_records_for_month(today.year, today.month)
    payments_matched = check_corp_payments(today.year, today.month)

    result = (
        f'{ore_new} new ore types, '
        f'{synced_chars} personal entries, '
        f'{synced_corps} corp observer entries, '
        f'{priced} prices updated, '
        f'{sov_systems} sovereignty systems tracked, '
        f'{billing_saved} billing records saved, '
        f'{payments_matched} payments automatically detected'
    )
    logger.info(f'Daily sync complete: {result}')
    return result


# Triggered when a new character registers in Alliance Auth
@shared_task
def sync_character_mining_task(character_id):
    """Syncs the mining ledger of a single character asynchronously."""
    try:
        from allianceauth.eveonline.models import EveCharacter
        from .services import sync_character_mining

        character = EveCharacter.objects.get(character_id=character_id)
        synced = sync_character_mining(character)
        logger.debug(f'Auto-sync for {character.character_name}: {synced} entries')
        return synced

    except Exception as e:
        logger.warning(f'Auto-sync failed for character {character_id}: {e}')
        return 0


# Triggered by the "Sync Now" button — runs in the background so the request
# doesn't time out on large datasets.
@shared_task
def manual_sync_task(user_id):
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning(f'manual_sync_task: user {user_id} not found')
        return 'user not found'

    from .services import sync_character_mining

    user_characters = [co.character for co in user.character_ownerships.all()]
    total_synced = 0
    for character in user_characters:
        try:
            total_synced += sync_character_mining(character)
        except Exception as e:
            logger.warning(f'Sync failed for {character.character_name}: {e}')

    corp_synced = sync_all_corp_observers()
    priced = update_market_prices()

    result = (
        f'{total_synced} personal + {corp_synced} corp entries synced, '
        f'{priced} prices updated'
    )
    logger.info(f'Manual sync by {user.username} complete: {result}')
    return result


# Triggered by the "Check Payments Now" button — runs in the background.
@shared_task
def check_payments_task(year, month, requested_by=None):
    from .payments import check_corp_payments

    matched = check_corp_payments(year, month)

    logger.info(
        f'Manual payment check for {month:02d}/{year}' +
        (f' by {requested_by}' if requested_by else '') +
        f' complete: {matched} corp(s) marked as paid'
    )
    return matched