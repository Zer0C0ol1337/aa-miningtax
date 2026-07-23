import logging
from datetime import date

from celery import shared_task

from .services import (
    sync_all_characters, sync_all_corp_observers, update_market_prices,
    repair_unresolved_ledger_names,
)

logger = logging.getLogger(__name__)


# Daily sync: personal ledgers + corp observer + market prices + sovereignty
# + billing records + payment check
@shared_task
def daily_mining_sync_task():
    from .billing import save_billing_records_for_month
    from .payments import check_corp_payments
    from .services import sync_sov_systems, sync_ore_categories

    # Refreshed first so any ore added to EVE is classified before the
    # ledgers that reference it are priced and taxed.
    ore_new, ore_updated = sync_ore_categories()

    # Corp observers first: the personal sync subtracts what they report in
    # order to derive belt and anomaly mining, so it needs their figures to
    # already be in place for the day being processed.
    synced_corps = sync_all_corp_observers()
    synced_chars = sync_all_characters()

    # Before pricing: a location left as "Unknown (id)" by a failed lookup stays
    # that way forever otherwise, and a tax-free moon whose structure name never
    # resolved cannot be matched — so its ore is taxed with nothing on screen to
    # explain it.
    repaired = repair_unresolved_ledger_names()

    priced = update_market_prices()
    sov_systems = sync_sov_systems()

    today = date.today()
    billing_saved = save_billing_records_for_month(today.year, today.month)
    payments_matched = check_corp_payments(today.year, today.month)

    result = (
        f'{ore_new} new ore types, '
        f'{synced_chars} personal entries, '
        f'{repaired} names repaired, '
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

    # Corp observers first, same reasoning as the daily task: the personal sync
    # derives belt and anomaly mining by subtracting what the observers report,
    # so running it first would credit structure mining twice.
    corp_synced = sync_all_corp_observers()

    user_characters = [co.character for co in user.character_ownerships.all()]
    total_synced = 0
    for character in user_characters:
        try:
            total_synced += sync_character_mining(character)
        except Exception as e:
            logger.warning(f'Sync failed for {character.character_name}: {e}')
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


# ─── MANUALLY TRIGGERED MAINTENANCE ───────────────────────────────────────────
#
# These back the buttons in Settings. They are Celery tasks rather than direct
# calls for two reasons: the work is slow enough to time out a web request —
# the ore import alone makes one ESI call per group — and a task that runs
# inside the web process appears nowhere in Alliance Auth's task monitor, so
# there is no record of it having run, by whom, or whether it finished.


@shared_task
def sync_sov_systems_task(requested_by=None):
    """Refreshes the known systems list. Backs the Systems tab button."""
    from .services import sync_sov_systems

    count = sync_sov_systems(force_recovery=True)
    result = f'{count} system(s) tracked'
    logger.info(f'Sovereignty sync by {requested_by or "unknown"}: {result}')
    return result


@shared_task
def sync_ore_categories_task(requested_by=None):
    """Imports the ore list from ESI. Backs the Tax Rates tab button."""
    from .services import sync_ore_categories

    imported, updated = sync_ore_categories()
    result = f'{imported} new, {updated} updated'
    logger.info(f'Ore import by {requested_by or "unknown"}: {result}')
    return result


@shared_task
def repair_location_names_task(requested_by=None):
    """Re-resolves placeholder locations. Backs the Systems tab button."""
    from .services import repair_unresolved_ledger_names

    repaired = repair_unresolved_ledger_names()
    result = f'{repaired} name(s) resolved'
    logger.info(f'Location repair by {requested_by or "unknown"}: {result}')
    return result


@shared_task
def update_prices_task(requested_by=None):
    """Prices entries that have none. Backs the Pricing tab button."""
    from .services import update_market_prices

    updated = update_market_prices()
    result = f'{updated} entrie(s) priced'
    logger.info(f'Price update by {requested_by or "unknown"}: {result}')
    return result


@shared_task
def register_corporation_task(corporation_id, requested_by=None):
    """
    Registers one corporation with Alliance Auth. Backs the Settings button.

    A task rather than a direct call for the same reason as the rest: work that
    talks to ESI belongs off the web request, and an action nobody can see
    afterwards is an action nobody can troubleshoot.
    """
    from allianceauth.eveonline.models import EveCorporationInfo

    if EveCorporationInfo.objects.filter(corporation_id=corporation_id).exists():
        return 'already registered'

    try:
        corp = EveCorporationInfo.objects.create_corporation(corporation_id=corporation_id)
    except Exception as e:
        logger.warning(f'Could not register corporation {corporation_id}: {e}')
        return f'failed: {e}'

    logger.info(f'Corporation {corp.corporation_name} ({corporation_id}) registered by {requested_by or "unknown"}')
    return f'registered {corp.corporation_name}'


@shared_task
def register_alliance_corps_task(alliance_id, requested_by=None):
    """
    Registers every corporation of an alliance.

    This is why the registration actions became tasks at all: one ESI call
    fetches the corp list, then Alliance Auth makes another for each corp it
    does not know. An alliance of fifty corps is fifty-one requests, which is
    well past what a web request should be doing.
    """
    from allianceauth.eveonline.models import EveCorporationInfo
    from .services import _get_esi_client

    try:
        esi = _get_esi_client()
        corp_ids = esi.client.Alliance.GetAlliancesAllianceIdCorporations(
            alliance_id=alliance_id
        ).results()
    except Exception as e:
        logger.warning(f'Could not fetch corp list for alliance {alliance_id}: {e}')
        return f'failed: {e}'

    registered = 0
    already_present = 0
    failed = 0

    for corp_id in (corp_ids or []):
        if EveCorporationInfo.objects.filter(corporation_id=corp_id).exists():
            already_present += 1
            continue
        try:
            EveCorporationInfo.objects.create_corporation(corporation_id=corp_id)
            registered += 1
        except Exception as e:
            logger.warning(f'Could not register corp {corp_id} of alliance {alliance_id}: {e}')
            failed += 1

    result = f'{registered} new, {already_present} already present, {failed} failed'
    logger.info(f'Alliance {alliance_id} corps registered by {requested_by or "unknown"}: {result}')
    return result